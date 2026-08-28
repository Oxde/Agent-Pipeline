"""The referee.

Everything that can refuse a transition lives here. The engine does not do the
work and does not care who does — it only decides whether a phase is allowed to
open, and whether it is allowed to close.

Four things block a phase from closing, in this order:

1. its dependencies are not complete
2. its declared artifact does not exist, or is empty
3. a mechanical criterion failed when *the engine* ran it
4. a blocking judged or human criterion has no recorded verdict, or has a
   failing one

Order matters. Reporting "your criterion failed" when the artifact was never
written sends the author to the wrong place.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .ledger import Ledger, Verdict, now_iso
from .spec import Criterion, Phase, Pipeline

CHECK_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class Context:
    """Everything needed to turn a template into a real path."""

    root: Path
    run: str
    workdir: Path
    # Absolute path to the engine install. Built-in blocks reference their
    # checks as {engine}/checks/… so they keep working from any project, not
    # only from inside this repo.
    engine: Path = Path(__file__).resolve().parent
    vars: dict[str, str] = field(default_factory=dict)

    def render(self, template: str, phase: Phase | None = None) -> str:
        values: dict[str, str] = {
            "run": self.run,
            "workdir": str(self.workdir),
            "root": str(self.root),
            "engine": str(self.engine),
            **self.vars,
        }
        if phase is not None:
            values["phase"] = phase.id
        try:
            return template.format(**values)
        except KeyError as exc:
            raise GateError(
                f"cannot render {template!r}: no value for {exc.args[0]!r}. "
                f"Pass it with --var {exc.args[0]}=..., or add it to the pipeline's vars."
            ) from exc

    def context_paths(self, phase: Phase) -> list[Path]:
        out: list[Path] = []
        for template in phase.context:
            p = Path(self.render(template, phase))
            out.append(p if p.is_absolute() else self.root / p)
        return out

    def artifact_path(self, phase: Phase) -> Path | None:
        if phase.artifact is None:
            return None
        rendered = self.render(phase.artifact, phase)
        p = Path(rendered)
        return p if p.is_absolute() else (self.root / p)


class GateError(Exception):
    """A gate could not be evaluated at all (bad template, unrunnable check)."""


@dataclass
class Failure:
    code: str
    message: str
    hint: str = ""


@dataclass
class GateReport:
    phase: str
    ok: bool
    failures: list[Failure] = field(default_factory=list)
    artifact: Path | None = None
    stale_because: list[str] = field(default_factory=list)

    def add(self, code: str, message: str, hint: str = "") -> None:
        self.failures.append(Failure(code, message, hint))
        self.ok = False


# ---------------------------------------------------------------------------
# opening a phase
# ---------------------------------------------------------------------------

def check_can_start(
    pipeline: Pipeline, ledger: Ledger, phase: Phase, ctx: Context | None = None
) -> GateReport:
    report = GateReport(phase=phase.id, ok=True)
    for dep in phase.depends_on:
        if not ledger.is_complete(dep):
            dep_phase = pipeline.phase(dep)
            state = ledger.phases.get(dep)
            status = state.status if state else "pending"
            if dep_phase.optional and status == "pending":
                continue
            report.add(
                "dependency-incomplete",
                f"'{phase.id}' depends on '{dep}', which is {status}.",
                f"Run: start {dep} -> produce its artifact -> complete {dep}",
            )
    # Required reading must EXIST before work starts. Whether it was read is
    # unknowable from here; whether it is readable is not.
    if ctx is not None:
        for path in ctx.context_paths(phase):
            if not path.is_file():
                report.add(
                    "context-missing",
                    f"'{phase.id}' declares {path} as required reading — it does not exist.",
                    "Fix the path in the pipeline, or produce the file it points at.",
                )
    return report


# ---------------------------------------------------------------------------
# closing a phase
# ---------------------------------------------------------------------------

def check_can_complete(
    pipeline: Pipeline,
    ledger: Ledger,
    phase: Phase,
    ctx: Context,
    *,
    record: bool = True,
) -> GateReport:
    """Evaluate every gate on ``phase``. Mechanical checks actually run.

    ``record=True`` writes the mechanical verdicts into the ledger, which is
    what makes them auditable afterwards. Set it False for a dry read (``status``
    calls this to show what *would* block).
    """
    report = GateReport(phase=phase.id, ok=True)

    entry = ledger.phases.get(phase.id)
    if entry is None or entry.status == "pending":
        report.add(
            "not-started",
            f"'{phase.id}' was never started.",
            f"Run: start {phase.id}",
        )
        return report

    dep_report = check_can_start(pipeline, ledger, phase, ctx)
    report.failures.extend(dep_report.failures)
    report.ok = report.ok and dep_report.ok

    artifact = ctx.artifact_path(phase)
    report.artifact = artifact
    if artifact is not None:
        if not artifact.exists():
            report.add(
                "artifact-missing",
                f"'{phase.id}' declares artifact {artifact} — it does not exist.",
                "Produce the artifact, then re-run complete.",
            )
            return report
        if artifact.is_file() and artifact.stat().st_size == 0:
            report.add(
                "artifact-empty",
                f"'{phase.id}' artifact {artifact} exists but is 0 bytes.",
                "An empty file is not a completed phase.",
            )
            return report

    report.stale_because = stale_against(pipeline, phase, ctx)
    if report.stale_because:
        report.add(
            "artifact-stale",
            f"'{phase.id}' artifact is older than: {', '.join(report.stale_because)}.",
            "An upstream phase changed after this one was written. Rebuild it.",
        )

    for criterion in phase.blocking_criteria():
        _evaluate(criterion, phase, ledger, ctx, report, record=record)

    return report


def _evaluate(
    criterion: Criterion,
    phase: Phase,
    ledger: Ledger,
    ctx: Context,
    report: GateReport,
    *,
    record: bool,
) -> None:
    if criterion.kind == "mechanical":
        verdict = run_mechanical(criterion, phase, ctx)
        if record:
            ledger.record_verdict(phase.id, verdict)
        if not verdict.passed():
            report.add(
                "criterion-failed",
                f"[{criterion.id}] {criterion.description}",
                verdict.evidence.strip()[:600] or "(the check produced no output)",
            )
        return

    entry = ledger.phases.get(phase.id)
    panel = entry.current_panel(criterion.id, criterion.kind) if entry else []
    verb = "judge" if criterion.kind == "judged" else "approve"

    fails = [v for v in panel if not v.passed()]
    if fails:
        worst = max(fails, key=lambda v: v.recorded_at)
        report.add(
            "criterion-failed",
            f"[{criterion.id}] {criterion.description} — FAIL by {worst.by}.",
            worst.evidence.strip()[:600],
        )
        return

    # The panel holds at most one verdict per author (the ledger enforces it),
    # so len(panel) IS the count of distinct passing judges.
    need = criterion.independence
    if len(panel) < need:
        if not panel:
            msg = f"[{criterion.id}] {criterion.description} — no verdict recorded."
        else:
            who = ", ".join(v.by for v in panel)
            msg = (f"[{criterion.id}] {criterion.description} — {len(panel)}/{need} "
                   f"independent verdicts (so far: {who}).")
        hint = (f"{verb} {phase.id} {criterion.id} --status pass --evidence '...'"
                if need == 1 else
                f"{verb} {phase.id} {criterion.id} --status pass --by <distinct-name> "
                f"--evidence '...' — a repeated --by replaces, it does not add")
        report.add("criterion-unanswered", msg, hint)


def run_mechanical(criterion: Criterion, phase: Phase, ctx: Context) -> Verdict:
    """Run a mechanical check and turn its exit code into a verdict.

    The engine runs this itself, every time a phase closes. That is deliberate:
    a mechanical verdict carried over from a previous attempt is exactly how a
    check that used to pass keeps passing after the artifact changed underneath
    it.
    """
    if criterion.run is None:
        raise GateError(f"criterion '{criterion.id}' is mechanical but has no 'run'")

    artifact = ctx.artifact_path(phase)
    command = ctx.render(
        criterion.run.replace("{artifact}", str(artifact) if artifact else ""),
        phase,
    )
    try:
        proc = subprocess.run(
            shlex.split(command),
            cwd=ctx.root,
            capture_output=True,
            text=True,
            timeout=CHECK_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise GateError(
            f"criterion '{criterion.id}' runs {command!r}, which does not exist ({exc.strerror}). "
            f"A check that cannot run is not a check that passes."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        return Verdict(
            criterion=criterion.id,
            kind="mechanical",
            status="fail",
            evidence=f"timed out after {CHECK_TIMEOUT_SECONDS}s: {command}\n{exc.stdout or ''}",
            recorded_at=now_iso(),
            by="engine",
        )

    evidence = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return Verdict(
        criterion=criterion.id,
        kind="mechanical",
        status="pass" if proc.returncode == 0 else "fail",
        evidence=evidence or f"exit {proc.returncode}: {command}",
        recorded_at=now_iso(),
        by="engine",
    )


# ---------------------------------------------------------------------------
# staleness
# ---------------------------------------------------------------------------

def stale_against(pipeline: Pipeline, phase: Phase, ctx: Context) -> list[str]:
    """Upstream artifacts written *after* this phase's artifact.

    This is the check that would have caught the reviewed-the-old-cut bug: the
    master existed, every phase was green, and the only thing wrong was that an
    input had moved on since. Existence was never the right question.
    """
    mine = ctx.artifact_path(phase)
    if mine is None or not mine.exists():
        return []
    my_mtime = mine.stat().st_mtime
    newer: list[str] = []
    for dep_id in phase.depends_on:
        dep = pipeline.phase(dep_id)
        dep_path = ctx.artifact_path(dep)
        if dep_path is None or not dep_path.exists():
            continue
        if dep_path.stat().st_mtime > my_mtime:
            newer.append(dep_id)
    return newer


# ---------------------------------------------------------------------------
# shipping
# ---------------------------------------------------------------------------

def check_can_ship(pipeline: Pipeline, ledger: Ledger, ctx: Context) -> GateReport:
    """The last refusal: every required phase complete, nothing stale."""
    report = GateReport(phase="<ship>", ok=True)
    for phase in pipeline.phases:
        entry = ledger.phases.get(phase.id)
        status = entry.status if entry else "pending"
        stale = stale_against(pipeline, phase, ctx)
        if status != "complete":
            if phase.optional:
                continue
            hint = (
                f"Finish rebuilding {phase.id}, then run: complete {phase.id}"
                if status == "active"
                else f"Run: start {phase.id} → produce its artifact → complete {phase.id}"
            )
            report.add(
                "phase-incomplete",
                f"required phase '{phase.id}' ({phase.name}) is {status}; only completed phases can ship.",
                hint,
            )
            if stale:
                report.add(
                    "phase-stale",
                    f"'{phase.id}' is {status}, and its artifact predates: {', '.join(stale)}.",
                    f"Rebuild {phase.id} from the current upstream artifact, then complete it.",
                )
            continue
        if stale:
            report.add(
                "phase-stale",
                f"'{phase.id}' completed, but its artifact predates: {', '.join(stale)}.",
                f"Run: reopen {phase.id} --reason 'upstream changed'",
            )
        if entry is not None and entry.forced:
            report.add(
                "phase-forced",
                f"'{phase.id}' was force-completed, so its gates never passed.",
                "Re-run it properly, or ship with --allow-forced and own it.",
            )
    return report

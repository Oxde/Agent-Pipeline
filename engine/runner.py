"""Runner mode: the engine owns the loop.

In referee mode an agent walks the pipeline and this engine only refuses bad
transitions. That is the right shape when one agent needs the whole scope in
its head — a long creative piece where the ending has to answer the opening.

Runner mode is the other shape, and it exists for the unattended case: crons,
batches, anything where nobody is watching. Here the *engine* holds the loop and
calls out per phase. An agent invoked this way cannot skip a step, because it
never decides what comes next.

Fail-closed by design. The first phase that cannot close stops the run and
leaves the ledger exactly where it stopped, so resuming is just running again.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .gates import Context, GateReport, check_can_complete, check_can_start
from .ledger import Ledger, now_iso
from .spec import Phase, Pipeline

DEFAULT_PHASE_TIMEOUT = 3600


@dataclass
class StepResult:
    phase: str
    ok: bool
    skipped: bool = False
    exit_code: int | None = None
    report: GateReport | None = None
    output: str = ""


@dataclass
class RunResult:
    completed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    stopped_at: str | None = None
    steps: list[StepResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.stopped_at is None


def run_pipeline(
    pipeline: Pipeline,
    ledger: Ledger,
    ctx: Context,
    *,
    start_from: str | None = None,
    only: str | None = None,
    timeout: int = DEFAULT_PHASE_TIMEOUT,
    on_event: object = None,
) -> RunResult:
    """Walk the pipeline, executing each phase's ``run`` command.

    Phases already complete are skipped, which is what makes a re-run cheap:
    the expensive parts stay done and only what actually changed re-executes.
    """
    result = RunResult()
    began = start_from is None

    for phase in pipeline.phases:
        if only is not None and phase.id != only:
            continue
        if not began:
            if phase.id == start_from:
                began = True
            else:
                continue

        if ledger.is_complete(phase.id):
            result.skipped.append(phase.id)
            result.steps.append(StepResult(phase=phase.id, ok=True, skipped=True))
            _emit(on_event, "cached", phase, None)
            continue

        start_report = check_can_start(pipeline, ledger, phase)
        if not start_report.ok:
            result.stopped_at = phase.id
            result.steps.append(StepResult(phase=phase.id, ok=False, report=start_report))
            _emit(on_event, "blocked", phase, start_report)
            return result

        if phase.run is None:
            if phase.optional:
                result.skipped.append(phase.id)
                result.steps.append(StepResult(phase=phase.id, ok=True, skipped=True))
                continue
            report = GateReport(phase=phase.id, ok=True)
            report.add(
                "no-command",
                f"runner mode reached '{phase.id}', which has no 'run' command.",
                "Give the phase a `run:`, mark it `optional: true`, or use referee mode.",
            )
            result.stopped_at = phase.id
            result.steps.append(StepResult(phase=phase.id, ok=False, report=report))
            _emit(on_event, "blocked", phase, report)
            return result

        entry = ledger.entry(phase.id)
        entry.status = "active"
        entry.started_at = now_iso()
        ledger.save(ctx.workdir)
        _emit(on_event, "start", phase, None)

        step = _execute(phase, ctx, timeout)
        result.steps.append(step)

        report = check_can_complete(pipeline, ledger, phase, ctx)
        step.report = report

        if step.exit_code != 0:
            report.add(
                "command-failed",
                f"'{phase.id}' command exited {step.exit_code}.",
                step.output.strip()[-600:],
            )

        if not report.ok:
            ledger.save(ctx.workdir)
            result.stopped_at = phase.id
            step.ok = False
            _emit(on_event, "blocked", phase, report)
            return result

        entry.status = "complete"
        entry.completed_at = now_iso()
        artifact = ctx.artifact_path(phase)
        entry.artifact = str(artifact) if artifact else None
        ledger.save(ctx.workdir)
        result.completed.append(phase.id)
        step.ok = True
        _emit(on_event, "complete", phase, None)

    return result


def _execute(phase: Phase, ctx: Context, timeout: int) -> StepResult:
    assert phase.run is not None
    artifact = ctx.artifact_path(phase)
    if artifact is not None:
        artifact.parent.mkdir(parents=True, exist_ok=True)
    command = ctx.render(
        phase.run.replace("{artifact}", str(artifact) if artifact else ""),
        phase,
    )
    try:
        proc = subprocess.run(
            shlex.split(command),
            cwd=ctx.root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return StepResult(
            phase=phase.id, ok=False, exit_code=127,
            output=f"command not found: {command} ({exc.strerror})",
        )
    except subprocess.TimeoutExpired:
        return StepResult(
            phase=phase.id, ok=False, exit_code=124,
            output=f"timed out after {timeout}s: {command}",
        )
    return StepResult(
        phase=phase.id,
        ok=proc.returncode == 0,
        exit_code=proc.returncode,
        output=((proc.stdout or "") + (proc.stderr or "")),
    )


def _emit(on_event: object, kind: str, phase: Phase, report: GateReport | None) -> None:
    if callable(on_event):
        on_event(kind, phase, report)

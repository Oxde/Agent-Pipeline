"""Run state: what has happened, what was checked, and who said so.

The ledger is a plain JSON file under ``<workdir>/.pipeline/``. One entry per
phase; inside each, a **panel** of verdicts per criterion — at most one verdict
per author, so a criterion demanding three independent judges holds three
verdicts from three distinct ``by`` values, and one voice cannot pretend to be
a panel by recording twice.

The design rule that matters: **a claim is not a verdict.** Nothing in here is
written because someone asserted a step was done. Mechanical verdicts are
written by the engine after it ran the command itself; judged and human
verdicts arrive only through an explicit ``judge`` / ``approve`` call carrying
evidence. A phase with an unanswered blocking criterion cannot close, and no
amount of confidence substitutes for the record.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from ._version import __version__

PhaseStatus = Literal["pending", "active", "complete"]
VerdictStatus = Literal["pass", "fail"]

LEDGER_DIRNAME = ".pipeline"
LEDGER_FILENAME = "ledger.json"
# v2: verdicts became panels (list per criterion, one per author). v1 ledgers
# are migrated silently on load — each lone verdict becomes a panel of one.
SCHEMA_VERSION = 2


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LedgerError(Exception):
    """The ledger on disk is unusable or the requested transition is illegal."""


@dataclass
class Verdict:
    """One author's recorded answer to one criterion."""

    criterion: str
    kind: str
    status: VerdictStatus
    evidence: str
    recorded_at: str
    by: str

    def passed(self) -> bool:
        return self.status == "pass"


@dataclass
class PhaseEntry:
    status: PhaseStatus = "pending"
    started_at: str | None = None
    completed_at: str | None = None
    artifact: str | None = None
    iteration: int = 1
    forced: bool = False
    # criterion id -> panel. Invariant: at most one verdict per author, kept by
    # record(); re-judging replaces your own answer, never a colleague's.
    verdicts: dict[str, list[Verdict]] = field(default_factory=dict)
    history: list[dict[str, object]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    cost_usd: float = 0.0

    def panel(self, criterion_id: str) -> list[Verdict]:
        return self.verdicts.get(criterion_id, [])

    def current_panel(self, criterion_id: str, kind: str) -> list[Verdict]:
        """Verdicts that answer the criterion's current kind.

        Kind-mismatched entries remain in ``verdicts`` as inactive historical
        evidence, but they cannot satisfy, fail, or add independence to a gate.
        """
        return [verdict for verdict in self.panel(criterion_id) if verdict.kind == kind]

    def verdict(self, criterion_id: str) -> Verdict | None:
        """The newest verdict on a criterion, any author — the headline view."""
        p = self.panel(criterion_id)
        return max(p, key=lambda v: v.recorded_at) if p else None

    def record(self, verdict: Verdict) -> None:
        panel = self.verdicts.setdefault(verdict.criterion, [])
        replaced = next((v for v in panel if v.by == verdict.by), None)
        # Mechanical-to-mechanical refreshes are routine and need not grow the
        # ledger. Preserve a replacement if either side is a human decision.
        if (
            replaced is not None
            and (replaced.kind != "mechanical" or verdict.kind != "mechanical")
        ):
            self.history.append(
                {
                    "event": "verdict-replaced",
                    "criterion": verdict.criterion,
                    "by": verdict.by,
                    "previous": asdict(replaced),
                    "replacement": asdict(verdict),
                    "archived_at": now_iso(),
                }
            )
        panel[:] = [v for v in panel if v.by != verdict.by]
        panel.append(verdict)


def _entry_dict(e: PhaseEntry) -> dict[str, object]:
    d = asdict(e)
    d["verdicts"] = {cid: [asdict(v) for v in panel] for cid, panel in e.verdicts.items()}
    return d


def _entry_from(raw: dict[str, object], where: str = "ledger") -> PhaseEntry:
    """Build a PhaseEntry from persisted JSON — treated as external input.

    The engine only ever writes consistent panels, so an inconsistency here
    means the file was hand-edited or corrupted. Two cases, two responses:

    - a verdict filed under one criterion key whose own ``criterion`` field
      names another is unresolvable ambiguity: fail closed (LedgerError),
      same as invalid JSON — refusing to guess at run state beats guessing.
    - duplicate authors in one panel would let a single voice inflate an
      ``independence`` tally: normalize to the newest verdict per author,
      archiving differing discards to history so the edit stays visible.
    """
    history = list(raw.get("history") or [])
    verdicts: dict[str, list[Verdict]] = {}
    for cid, panel in (raw.get("verdicts") or {}).items():
        # v1 stored one verdict per criterion; v2 stores a panel.
        items = panel if isinstance(panel, list) else [panel]
        loaded = [Verdict(**v) for v in items]
        for v in loaded:
            if v.criterion != cid:
                raise LedgerError(
                    f"{where}: a verdict filed under criterion '{cid}' claims to be "
                    f"for '{v.criterion}' (by {v.by}). The file is hand-edited or "
                    f"corrupt — fix it; the engine will not guess which id is real."
                )
        deduped: list[Verdict] = []
        for v in loaded:
            prior = next((x for x in deduped if x.by == v.by), None)
            if prior is None:
                deduped.append(v)
                continue
            keep, drop = (v, prior) if v.recorded_at >= prior.recorded_at else (prior, v)
            if asdict(drop) != asdict(keep):
                history.append({
                    "event": "verdict-duplicate-discarded",
                    "criterion": cid,
                    "by": drop.by,
                    "discarded": asdict(drop),
                    "kept": asdict(keep),
                    "archived_at": now_iso(),
                })
            deduped[:] = [x for x in deduped if x.by != v.by]
            deduped.append(keep)
        verdicts[cid] = deduped
    return PhaseEntry(
        status=raw.get("status", "pending"),
        started_at=raw.get("started_at"),
        completed_at=raw.get("completed_at"),
        artifact=raw.get("artifact"),
        iteration=int(raw.get("iteration", 1)),
        forced=bool(raw.get("forced", False)),
        verdicts=verdicts,
        history=history,
        notes=list(raw.get("notes") or []),
        cost_usd=float(raw.get("cost_usd", 0.0)),
    )


@dataclass
class Ledger:
    pipeline: str
    run: str
    created_at: str
    schema: int = SCHEMA_VERSION
    iteration: int = 1
    # The --var values this run was started with, so a later command (a report,
    # another session) can rebuild artifact paths without being handed them again.
    vars: dict[str, str] = field(default_factory=dict)
    phases: dict[str, PhaseEntry] = field(default_factory=dict)

    # -- persistence --------------------------------------------------------

    @staticmethod
    def path_for(workdir: Path) -> Path:
        return workdir / LEDGER_DIRNAME / LEDGER_FILENAME

    @staticmethod
    def load(workdir: Path, pipeline: str, run: str) -> "Ledger":
        path = Ledger.path_for(workdir)
        if not path.is_file():
            return Ledger(pipeline=pipeline, run=run, created_at=now_iso())
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise LedgerError(
                f"{path} is not valid JSON ({exc}). Refusing to guess at run state — "
                f"fix or delete the file."
            ) from exc
        if raw.get("schema") not in (1, SCHEMA_VERSION):
            raise LedgerError(
                f"{path}: ledger schema {raw.get('schema')!r}, this engine reads 1–"
                f"{SCHEMA_VERSION}. Start a new run."
            )
        return Ledger(
            pipeline=raw.get("pipeline", pipeline),
            run=raw.get("run", run),
            created_at=raw.get("created_at", now_iso()),
            schema=SCHEMA_VERSION,
            iteration=int(raw.get("iteration", 1)),
            vars={str(k): str(v) for k, v in (raw.get("vars") or {}).items()},
            phases={
                pid: _entry_from(e, where=f"{path} phase '{pid}'")
                for pid, e in (raw.get("phases") or {}).items()
            },
        )

    def save(self, workdir: Path) -> Path:
        path = Ledger.path_for(workdir)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": SCHEMA_VERSION,
            "engine": __version__,
            "pipeline": self.pipeline,
            "run": self.run,
            "created_at": self.created_at,
            "iteration": self.iteration,
            "vars": self.vars,
            "phases": {pid: _entry_dict(e) for pid, e in self.phases.items()},
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return path

    # -- accessors ----------------------------------------------------------

    def entry(self, phase_id: str) -> PhaseEntry:
        return self.phases.setdefault(phase_id, PhaseEntry())

    def is_complete(self, phase_id: str) -> bool:
        return self.phases.get(phase_id, PhaseEntry()).status == "complete"

    def total_cost(self) -> float:
        return round(sum(e.cost_usd for e in self.phases.values()), 4)

    # -- transitions --------------------------------------------------------

    def record_verdict(self, phase_id: str, verdict: Verdict) -> None:
        self.entry(phase_id).record(verdict)

    def absorb_external(self, workdir: Path) -> list[str]:
        """Merge in changes a subprocess made to the ledger on disk.

        Runner mode holds this object in memory while a phase command runs, and
        that command may itself call ``judge``, ``approve`` or ``cost`` — an
        agent recording its own verdict is the normal way an unattended
        pipeline satisfies a judged criterion. Without this merge the next
        save silently overwrites those writes, and the phase blocks forever on
        a verdict it actually has. Returns what was absorbed.
        """
        path = Ledger.path_for(workdir)
        if not path.is_file():
            return []
        try:
            disk = Ledger.load(workdir, self.pipeline, self.run)
        except LedgerError:
            return []

        absorbed: list[str] = []
        for pid, their in disk.phases.items():
            mine = self.entry(pid)
            for cid, panel in their.verdicts.items():
                for verdict in panel:
                    current = next(
                        (v for v in mine.panel(cid) if v.by == verdict.by), None
                    )
                    if (
                        current is None
                        or verdict.recorded_at > current.recorded_at
                        or (
                            verdict.recorded_at == current.recorded_at
                            and verdict != current
                        )
                    ):
                        mine.verdicts[cid] = [
                            v for v in mine.panel(cid) if v.by != verdict.by
                        ]
                        mine.verdicts[cid].append(verdict)
                        absorbed.append(f"{pid}/{cid}@{verdict.by}")
            for event in their.history:
                if event not in mine.history:
                    mine.history.append(event)
            if their.cost_usd != mine.cost_usd:
                mine.cost_usd = their.cost_usd
            for note in their.notes:
                if note not in mine.notes:
                    mine.notes.append(note)
        return absorbed

    def snapshot(self, phase_id: str, reason: str) -> None:
        """Archive the current attempt before it is invalidated.

        Iterations are never destroyed — the reason a rewrite worked is usually
        only visible against the thing it replaced.
        """
        e = self.entry(phase_id)
        e.history.append(
            {
                "iteration": e.iteration,
                "status": e.status,
                "artifact": e.artifact,
                "started_at": e.started_at,
                "completed_at": e.completed_at,
                "verdicts": {
                    cid: [asdict(v) for v in panel] for cid, panel in e.verdicts.items()
                },
                "reason": reason,
                "archived_at": now_iso(),
            }
        )

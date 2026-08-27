"""Run state: what has happened, what was checked, and who said so.

The ledger is a plain JSON file under ``<workdir>/.pipeline/``. It holds one
entry per phase and, inside each, one *verdict* per criterion.

The design rule that matters: **a claim is not a verdict.** Nothing in here is
written because someone asserted a step was done. Mechanical verdicts are
written by the engine after it ran the command itself; judged and human
verdicts are written only through an explicit ``judge`` / ``approve`` call that
has to carry evidence. A phase with an unanswered blocking criterion cannot
close, and no amount of confidence substitutes for the record.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

PhaseStatus = Literal["pending", "active", "complete"]
VerdictStatus = Literal["pass", "fail"]

LEDGER_DIRNAME = ".pipeline"
LEDGER_FILENAME = "ledger.json"
SCHEMA_VERSION = 1


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LedgerError(Exception):
    """The ledger on disk is unusable or the requested transition is illegal."""


@dataclass
class Verdict:
    """One recorded answer to one criterion."""

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
    verdicts: dict[str, Verdict] = field(default_factory=dict)
    history: list[dict[str, object]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    cost_usd: float = 0.0

    def verdict(self, criterion_id: str) -> Verdict | None:
        return self.verdicts.get(criterion_id)


@dataclass
class Ledger:
    pipeline: str
    run: str
    created_at: str
    schema: int = SCHEMA_VERSION
    iteration: int = 1
    # The --var values this run was started with. Recorded so a later command
    # can rebuild artifact paths without being handed them again.
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
        if raw.get("schema") != SCHEMA_VERSION:
            raise LedgerError(
                f"{path}: ledger schema {raw.get('schema')!r}, this engine writes "
                f"{SCHEMA_VERSION}. Migrate or start a new run."
            )
        phases: dict[str, PhaseEntry] = {}
        for pid, entry in (raw.get("phases") or {}).items():
            verdicts = {
                vid: Verdict(**v) for vid, v in (entry.get("verdicts") or {}).items()
            }
            phases[pid] = PhaseEntry(
                status=entry.get("status", "pending"),
                started_at=entry.get("started_at"),
                completed_at=entry.get("completed_at"),
                artifact=entry.get("artifact"),
                iteration=int(entry.get("iteration", 1)),
                forced=bool(entry.get("forced", False)),
                verdicts=verdicts,
                history=list(entry.get("history") or []),
                notes=list(entry.get("notes") or []),
                cost_usd=float(entry.get("cost_usd", 0.0)),
            )
        return Ledger(
            pipeline=raw.get("pipeline", pipeline),
            run=raw.get("run", run),
            created_at=raw.get("created_at", now_iso()),
            schema=SCHEMA_VERSION,
            iteration=int(raw.get("iteration", 1)),
            vars={str(k): str(v) for k, v in (raw.get("vars") or {}).items()},
            phases=phases,
        )

    def save(self, workdir: Path) -> Path:
        path = Ledger.path_for(workdir)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": self.schema,
            "pipeline": self.pipeline,
            "run": self.run,
            "created_at": self.created_at,
            "iteration": self.iteration,
            "vars": self.vars,
            "phases": {
                pid: {
                    "status": e.status,
                    "started_at": e.started_at,
                    "completed_at": e.completed_at,
                    "artifact": e.artifact,
                    "iteration": e.iteration,
                    "forced": e.forced,
                    "verdicts": {vid: asdict(v) for vid, v in e.verdicts.items()},
                    "history": e.history,
                    "notes": e.notes,
                    "cost_usd": e.cost_usd,
                }
                for pid, e in self.phases.items()
            },
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

    def absorb_external(self, workdir: Path) -> list[str]:
        """Merge in changes a subprocess made to the ledger on disk.

        Runner mode holds this object in memory while a phase command runs, and
        that command may itself call `judge`, `approve` or `cost` — an agent
        recording its own verdict is the normal way an unattended pipeline
        satisfies a judged criterion. Without this merge the next save silently
        overwrites those writes, and the phase blocks forever on a verdict that
        was recorded and then destroyed.

        Returns the ids of what was absorbed, so the caller can say so.
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
            for cid, verdict in their.verdicts.items():
                current = mine.verdicts.get(cid)
                # Newest recorded verdict wins. A subprocess that just answered
                # a criterion is more current than whatever we loaded earlier.
                if current is None or verdict.recorded_at >= current.recorded_at:
                    if current is None or current.recorded_at != verdict.recorded_at:
                        absorbed.append(f"{pid}/{cid}")
                    mine.verdicts[cid] = verdict
            if their.cost_usd != mine.cost_usd:
                mine.cost_usd = their.cost_usd
            for note in their.notes:
                if note not in mine.notes:
                    mine.notes.append(note)
        return absorbed

    def record_verdict(self, phase_id: str, verdict: Verdict) -> None:
        self.entry(phase_id).verdicts[verdict.criterion] = verdict

    def snapshot(self, phase_id: str, reason: str) -> None:
        """Push the current attempt into ``history`` before it is overwritten.

        Iterations are never destroyed. A creative phase that took five passes
        should still be able to show you what pass two said, because the reason
        a rewrite worked is usually only visible against what it replaced.
        """
        e = self.entry(phase_id)
        e.history.append(
            {
                "iteration": e.iteration,
                "status": e.status,
                "artifact": e.artifact,
                "started_at": e.started_at,
                "completed_at": e.completed_at,
                "verdicts": {vid: asdict(v) for vid, v in e.verdicts.items()},
                "reason": reason,
                "archived_at": now_iso(),
            }
        )

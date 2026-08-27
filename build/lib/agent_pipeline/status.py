"""Rendering: the one view of a run.

Deliberately plain text. A pipeline you can only inspect through a web app is a
pipeline you cannot inspect from a cron log, an SSH session, or an agent's
transcript — which are the three places you most need to know what happened.
"""

from __future__ import annotations

from .gates import Context, GateReport, check_can_complete, stale_against
from .ledger import Ledger
from .spec import Phase, Pipeline

MARK = {"complete": "✓", "active": "◐", "pending": "○"}
RULE = "─" * 74


def _phase_mark(phase: Phase, ledger: Ledger, stale: bool) -> str:
    entry = ledger.phases.get(phase.id)
    status = entry.status if entry else "pending"
    if status == "complete" and stale:
        return "⚠"
    return MARK[status]


def render_status(pipeline: Pipeline, ledger: Ledger, ctx: Context, *, verbose: bool = False) -> str:
    lines: list[str] = []
    lines.append("")
    lines.append(f"  {pipeline.name}  ·  run={ledger.run}  ·  mode={pipeline.mode}  ·  v{ledger.iteration}")
    lines.append(f"  {RULE}")

    blocked: list[GateReport] = []
    for phase in pipeline.phases:
        entry = ledger.phases.get(phase.id)
        status = entry.status if entry else "pending"
        stale = bool(stale_against(pipeline, phase, ctx)) if status == "complete" else False
        mark = _phase_mark(phase, ledger, stale)

        tail = ""
        if status == "complete":
            tail = (entry.completed_at or "")[:19].replace("T", " ") if entry else ""
            if stale:
                tail = "STALE — upstream moved"
        elif status == "active":
            tail = "in progress"
        elif phase.optional:
            tail = "(optional)"

        flags: list[str] = []
        if phase.block:
            flags.append(phase.block)
        if entry and entry.forced:
            flags.append("FORCED")
        if entry and entry.iteration > 1:
            flags.append(f"v{entry.iteration}")
        if entry and entry.cost_usd:
            flags.append(f"${entry.cost_usd:.2f}")
        flag_s = f"  [{' · '.join(flags)}]" if flags else ""

        lines.append(f"  {mark}  {phase.id:<26} {phase.name:<28} {tail}{flag_s}")

        if verbose and phase.criteria:
            for c in phase.criteria:
                v = entry.verdict(c.id) if entry else None
                if v is None:
                    sym, note = "·", "no verdict"
                else:
                    sym = "✓" if v.passed() else "✗"
                    note = f"{v.status} by {v.by}"
                opt = "" if c.blocking else " (advisory)"
                lines.append(f"        {sym} {c.id:<24} {c.kind:<11} {note}{opt}")

        if status == "active":
            report = check_can_complete(pipeline, ledger, phase, ctx, record=False)
            if not report.ok:
                blocked.append(report)

    lines.append(f"  {RULE}")

    required = [p for p in pipeline.phases if not p.optional]
    done = [p for p in required if ledger.is_complete(p.id)]
    cost = ledger.total_cost()
    cost_s = f"  ·  ${cost:.2f} spent" if cost else ""
    lines.append(f"  {len(done)}/{len(required)} required phases complete{cost_s}")

    for report in blocked:
        lines.append("")
        lines.append(f"  '{report.phase}' cannot complete yet:")
        for f in report.failures:
            lines.append(f"    ✗ {f.message}")
            if f.hint:
                for hl in f.hint.splitlines()[:4]:
                    lines.append(f"      → {hl}")

    lines.append("")
    return "\n".join(lines)


def render_report(report: GateReport, *, title: str) -> str:
    if report.ok:
        return f"  ✓ {title}"
    out = ["", f"  ⛔ {title}", f"  {RULE}"]
    for f in report.failures:
        out.append(f"  ✗ [{f.code}] {f.message}")
        if f.hint:
            for hl in f.hint.splitlines()[:6]:
                out.append(f"      → {hl}")
    out.append(f"  {RULE}")
    out.append("")
    return "\n".join(out)

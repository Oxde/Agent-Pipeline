"""Draw the pipeline.

Two outputs, both plain text, neither needing a server or a browser build step:

``mermaid``  GitHub, GitLab and most markdown viewers render this natively, so a
             generated diagram can live directly in a README and stay honest —
             it is emitted from the same YAML the engine executes, so it cannot
             drift from the pipeline the way a hand-drawn picture does.
``dot``      Graphviz, when you want an SVG or PNG.

Pass a ledger and the same graph is coloured by live run state: what is done,
what is running, what is stale, what is blocked and why.
"""

from __future__ import annotations

import re
from typing import Literal

from .gates import Context, stale_against
from .ledger import Ledger
from .spec import Phase, Pipeline

Format = Literal["mermaid", "dot"]
NodeState = Literal["done", "active", "stale", "blocked", "pending", "optional"]

# Readable in both GitHub themes — light fills with darker strokes, which
# survive dark mode far better than saturated blocks do.
STYLE: dict[NodeState, tuple[str, str, str]] = {
    #                fill       stroke     text
    "done":     ("#E1F5EE", "#1D9E75", "#0F6E56"),
    "active":   ("#FAEEDA", "#BA7517", "#854F0B"),
    "stale":    ("#FCEBEB", "#E24B4A", "#A32D2D"),
    "blocked":  ("#F1EFE8", "#888780", "#5F5E5A"),
    "pending":  ("#F1EFE8", "#B4B2A9", "#5F5E5A"),
    "optional": ("#FFFFFF", "#B4B2A9", "#888780"),
}

MARK: dict[NodeState, str] = {
    "done": "✓", "active": "◐", "stale": "⚠",
    "blocked": "○", "pending": "○", "optional": "○",
}


def _safe(node_id: str) -> str:
    """Mermaid and DOT both dislike dots and dashes in bare node ids."""
    return "n_" + re.sub(r"[^A-Za-z0-9_]", "_", node_id)


def _escape(text: str) -> str:
    return text.replace('"', "'").replace("\n", " ")


def phase_state(
    pipeline: Pipeline,
    phase: Phase,
    ledger: Ledger | None,
    ctx: Context | None,
) -> NodeState:
    """What colour this node should be, given the run so far.

    With no ledger this is a structural drawing — everything is 'pending' and
    the diagram is about shape. With one, it is a picture of the actual run.
    """
    if ledger is None:
        return "optional" if phase.optional else "pending"
    entry = ledger.phases.get(phase.id)
    status = entry.status if entry else "pending"
    if status == "complete":
        if ctx is not None and stale_against(pipeline, phase, ctx):
            return "stale"
        return "done"
    if status == "active":
        return "active"
    if phase.depends_on and not all(ledger.is_complete(d) for d in phase.depends_on):
        return "blocked"
    return "optional" if phase.optional else "pending"


def _states(
    pipeline: Pipeline, ledger: Ledger | None, ctx: Context | None
) -> dict[str, NodeState]:
    return {p.id: phase_state(pipeline, p, ledger, ctx) for p in pipeline.phases}


def _label(phase: Phase, state: NodeState, *, with_state: bool) -> str:
    head = f"{MARK[state]} {phase.id}" if with_state else phase.id
    lines = [head]
    if phase.block:
        lines.append(f"<i>{phase.block}</i>")
    n = len(phase.blocking_criteria())
    if n:
        lines.append(f"{n} gate{'s' if n != 1 else ''}")
    return _escape("<br/>".join(lines))


def to_mermaid(
    pipeline: Pipeline,
    ledger: Ledger | None = None,
    ctx: Context | None = None,
    *,
    direction: str = "LR",
) -> str:
    states = _states(pipeline, ledger, ctx)
    with_state = ledger is not None
    out: list[str] = [f"flowchart {direction}"]

    for phase in pipeline.phases:
        state = states[phase.id]
        shape = ("([", "])") if phase.optional else ("[", "]")
        out.append(
            f'    {_safe(phase.id)}{shape[0]}"{_label(phase, state, with_state=with_state)}"{shape[1]}'
        )

    out.append("")
    for phase in pipeline.phases:
        for dep in phase.depends_on:
            out.append(f"    {_safe(dep)} --> {_safe(phase.id)}")

    out.append("")
    used = sorted({states[p.id] for p in pipeline.phases})
    for state in used:
        fill, stroke, text = STYLE[state]
        out.append(
            f"    classDef {state} fill:{fill},stroke:{stroke},stroke-width:1.5px,color:{text};"
        )
    for state in used:
        members = [_safe(p.id) for p in pipeline.phases if states[p.id] == state]
        if members:
            out.append(f"    class {','.join(members)} {state};")

    return "\n".join(out)


def to_dot(
    pipeline: Pipeline,
    ledger: Ledger | None = None,
    ctx: Context | None = None,
) -> str:
    states = _states(pipeline, ledger, ctx)
    with_state = ledger is not None
    out: list[str] = [
        f'digraph "{_escape(pipeline.name)}" {{',
        "    rankdir=LR;",
        '    node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11];',
        '    edge [color="#888780", arrowsize=0.7];',
    ]
    for phase in pipeline.phases:
        state = states[phase.id]
        fill, stroke, text = STYLE[state]
        label = _label(phase, state, with_state=with_state).replace("<br/>", "\\n")
        label = re.sub(r"</?i>", "", label)
        out.append(
            f'    {_safe(phase.id)} [label="{label}", fillcolor="{fill}", '
            f'color="{stroke}", fontcolor="{text}"];'
        )
    for phase in pipeline.phases:
        for dep in phase.depends_on:
            out.append(f"    {_safe(dep)} -> {_safe(phase.id)};")
    out.append("}")
    return "\n".join(out)


def render(
    pipeline: Pipeline,
    ledger: Ledger | None = None,
    ctx: Context | None = None,
    *,
    fmt: Format = "mermaid",
    direction: str = "LR",
) -> str:
    if fmt == "mermaid":
        return to_mermaid(pipeline, ledger, ctx, direction=direction)
    if fmt == "dot":
        return to_dot(pipeline, ledger, ctx)
    raise ValueError(f"unknown format {fmt!r} — expected 'mermaid' or 'dot'")

"""One HTML file showing every run.

No server, no build step, no CDN — a single file you open by double-clicking.
That constraint is the point: the person who most needs to see where a pipeline
is stuck is usually the person who will not run a terminal command to find out,
and a dashboard that needs `npm` is a dashboard nobody opens.

Layout is computed here in Python (a simple longest-path layering, same as the
wave calculation), so the diagram needs no JavaScript to draw itself.
"""

from __future__ import annotations

import html
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .gates import Context, check_can_complete, stale_against
from .ledger import LEDGER_DIRNAME, LEDGER_FILENAME, Ledger
from .spec import Pipeline

BOX_W, BOX_H = 158, 46
GAP_X, GAP_Y = 58, 16
PAD = 16

STATE_COLOR: dict[str, tuple[str, str, str]] = {
    "done":     ("#E1F5EE", "#1D9E75", "#0F6E56"),
    "active":   ("#FAEEDA", "#BA7517", "#854F0B"),
    "stale":    ("#FCEBEB", "#E24B4A", "#A32D2D"),
    "blocked":  ("#F4F3EE", "#B4B2A9", "#5F5E5A"),
    "pending":  ("#F4F3EE", "#CFCDC4", "#5F5E5A"),
}
STATE_LABEL = {
    "done": "done", "active": "in progress", "stale": "stale",
    "blocked": "waiting on a dependency", "pending": "not started",
}


@dataclass
class RunView:
    pipeline: Pipeline
    ledger: Ledger
    ctx: Context
    states: dict[str, str]
    blockers: dict[str, list[str]]

    @property
    def key(self) -> str:
        return f"{self.pipeline.name}::{self.ledger.run}"


def e(text: object) -> str:
    return html.escape(str(text), quote=True)


# ---------------------------------------------------------------------------
# gathering
# ---------------------------------------------------------------------------

def find_ledgers(root: Path, max_depth: int = 6) -> list[Path]:
    """Every ledger under `root`, without walking into the world."""
    found: list[Path] = []
    root = root.resolve()
    for path in root.rglob(f"{LEDGER_DIRNAME}/{LEDGER_FILENAME}"):
        rel = path.relative_to(root)
        if len(rel.parts) > max_depth + 2:
            continue
        if any(part in {"node_modules", ".git", ".venv", "venv"} for part in rel.parts):
            continue
        found.append(path)
    return sorted(found)


def build_view(pipeline: Pipeline, ledger: Ledger, ctx: Context) -> RunView:
    states: dict[str, str] = {}
    blockers: dict[str, list[str]] = {}
    for phase in pipeline.phases:
        entry = ledger.phases.get(phase.id)
        status = entry.status if entry else "pending"
        if status == "complete":
            states[phase.id] = "stale" if stale_against(pipeline, phase, ctx) else "done"
        elif status == "active":
            states[phase.id] = "active"
            # record=False: a report must never mutate the run it is describing.
            report = check_can_complete(pipeline, ledger, phase, ctx, record=False)
            if not report.ok:
                blockers[phase.id] = [f.message for f in report.failures]
        elif phase.depends_on and not all(ledger.is_complete(d) for d in phase.depends_on):
            states[phase.id] = "blocked"
        else:
            states[phase.id] = "pending"
    return RunView(pipeline=pipeline, ledger=ledger, ctx=ctx, states=states, blockers=blockers)


# ---------------------------------------------------------------------------
# the diagram
# ---------------------------------------------------------------------------

def _layout(pipeline: Pipeline) -> tuple[dict[str, tuple[int, int]], int, int]:
    depth: dict[str, int] = {}
    for phase in pipeline.phases:
        depth[phase.id] = 1 + max((depth[d] for d in phase.depends_on), default=0)
    bands: dict[int, list[str]] = defaultdict(list)
    for phase in pipeline.phases:
        bands[depth[phase.id]].append(phase.id)

    pos: dict[str, tuple[int, int]] = {}
    tallest = max((len(v) for v in bands.values()), default=1)
    for wave, ids in bands.items():
        span = len(ids) * BOX_H + (len(ids) - 1) * GAP_Y
        full = tallest * BOX_H + (tallest - 1) * GAP_Y
        top = PAD + (full - span) // 2
        for i, pid in enumerate(ids):
            x = PAD + (wave - 1) * (BOX_W + GAP_X)
            y = top + i * (BOX_H + GAP_Y)
            pos[pid] = (x, y)
    width = PAD * 2 + max(bands) * BOX_W + (max(bands) - 1) * GAP_X
    height = PAD * 2 + tallest * BOX_H + (tallest - 1) * GAP_Y
    return pos, width, height


def diagram(view: RunView) -> str:
    pos, width, height = _layout(view.pipeline)
    parts = [
        f'<svg class="dag" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-label="pipeline graph">'
    ]
    for phase in view.pipeline.phases:
        x2, y2 = pos[phase.id]
        for dep in phase.depends_on:
            x1, y1 = pos[dep]
            sx, sy = x1 + BOX_W, y1 + BOX_H // 2
            ex, ey = x2, y2 + BOX_H // 2
            mid = (sx + ex) / 2
            parts.append(
                f'<path d="M{sx} {sy} C{mid} {sy} {mid} {ey} {ex - 6} {ey}" '
                f'fill="none" stroke="#C9C7BE" stroke-width="1.5"/>'
                f'<path d="M{ex - 7} {ey - 3.5} L{ex} {ey} L{ex - 7} {ey + 3.5}" '
                f'fill="none" stroke="#C9C7BE" stroke-width="1.5" stroke-linejoin="round"/>'
            )
    for phase in view.pipeline.phases:
        x, y = pos[phase.id]
        state = view.states[phase.id]
        fill, stroke, text = STATE_COLOR[state]
        dash = ' stroke-dasharray="4 3"' if phase.optional else ""
        sub = phase.block or ("optional" if phase.optional else "")
        parts.append(
            f'<g><rect x="{x}" y="{y}" width="{BOX_W}" height="{BOX_H}" rx="6" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"{dash}/>'
            f'<text x="{x + 11}" y="{y + 20}" font-size="12.5" font-weight="500" fill="{text}">'
            f'{e(phase.id[:22])}</text>'
            f'<text x="{x + 11}" y="{y + 35}" font-size="10.5" fill="{text}" opacity="0.75">'
            f'{e(sub)}</text></g>'
        )
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------

CSS = """
:root{--bg:#F7F6F2;--card:#FFF;--ink:#26251F;--dim:#6C6A62;--line:#E6E4DC;--accent:#1D9E75}
@media(prefers-color-scheme:dark){:root{--bg:#16150F;--card:#201F18;--ink:#EDEBE1;--dim:#9C9A90;--line:#33322A}}
*{box-sizing:border-box}
body{margin:0;padding:32px 24px 64px;background:var(--bg);color:var(--ink);
 font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif}
.wrap{max-width:1120px;margin:0 auto}
h1{font-size:21px;font-weight:600;margin:0 0 4px}
.sub{color:var(--dim);font-size:13px;margin-bottom:28px}
.run{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px 22px;margin-bottom:22px}
.run>header{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:4px}
.run h2{font-size:17px;font-weight:600;margin:0}
.tag{font-size:12px;color:var(--dim)}
.bar{height:5px;background:var(--line);border-radius:3px;overflow:hidden;margin:12px 0 18px}
.bar>i{display:block;height:100%;background:var(--accent)}
.dagwrap{overflow-x:auto;padding-bottom:8px;margin-bottom:14px}
svg.dag{display:block}
table{width:100%;border-collapse:collapse;font-size:13.5px}
td{padding:8px 10px;border-top:1px solid var(--line);vertical-align:top}
tr:first-child td{border-top:0}
td.s{width:1%;white-space:nowrap}
.pill{display:inline-block;padding:1px 9px;border-radius:99px;font-size:11.5px;font-weight:500;white-space:nowrap}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
.dim{color:var(--dim)}
details{margin-top:6px}
summary{cursor:pointer;color:var(--dim);font-size:12.5px}
.block{margin-top:6px;padding:9px 12px;border-left:3px solid #E24B4A;background:#E24B4A14;border-radius:0 6px 6px 0;font-size:13px}
.v{display:flex;gap:9px;padding:4px 0;font-size:12.5px;align-items:baseline}
.v .k{color:var(--dim);min-width:82px}
.ev{color:var(--dim);font-style:italic}
.empty{color:var(--dim);padding:40px 0;text-align:center}
"""


def _phase_rows(view: RunView) -> str:
    rows: list[str] = []
    for phase in view.pipeline.phases:
        state = view.states[phase.id]
        fill, stroke, text = STATE_COLOR[state]
        entry = view.ledger.phases.get(phase.id)
        meta: list[str] = []
        if entry and entry.completed_at:
            meta.append(entry.completed_at[:16].replace("T", " "))
        if entry and entry.iteration > 1:
            meta.append(f"v{entry.iteration}")
        if entry and entry.cost_usd:
            meta.append(f"${entry.cost_usd:.2f}")
        if entry and entry.forced:
            meta.append("FORCED")

        detail = ""
        if phase.id in view.blockers:
            items = "".join(f"<div>• {e(m)}</div>" for m in view.blockers[phase.id])
            detail += f'<div class="block"><b>Blocked</b>{items}</div>'
        if entry and entry.verdicts:
            vs = []
            for cid, panel in entry.verdicts.items():
                for v in panel:
                    mark = "✓" if v.passed() else "✗"
                    vs.append(
                        f'<div class="v"><span class="k">{mark} {e(cid)}</span>'
                        f'<span class="ev">{e(v.evidence[:180] or "—")}</span>'
                        f'<span class="dim">— {e(v.by)}</span></div>'
                    )
            detail += (f'<details><summary>{len(vs)} verdict(s)</summary>'
                       f'{"".join(vs)}</details>')

        rows.append(
            f'<tr><td class="s"><span class="pill" style="background:{fill};color:{text};'
            f'border:1px solid {stroke}">{e(STATE_LABEL[state])}</span></td>'
            f'<td><span class="mono">{e(phase.id)}</span> '
            f'<span class="dim">{e(phase.name)}</span>{detail}</td>'
            f'<td class="s dim">{e(" · ".join(meta))}</td></tr>'
        )
    return "".join(rows)


def render_run(view: RunView) -> str:
    required = [p for p in view.pipeline.phases if not p.optional]
    done = [p for p in required if view.ledger.is_complete(p.id)]
    pct = round(100 * len(done) / len(required)) if required else 100
    stale = sum(1 for s in view.states.values() if s == "stale")
    cost = view.ledger.total_cost()

    tags = [f"run {view.ledger.run}", view.pipeline.mode, f"{len(done)}/{len(required)} phases"]
    if cost:
        tags.append(f"${cost:.2f}")
    if stale:
        tags.append(f"⚠ {stale} stale")

    return (
        f'<section class="run"><header><h2>{e(view.pipeline.name)}</h2>'
        f'<span class="tag">{e(" · ".join(tags))}</span></header>'
        f'<div class="bar"><i style="width:{pct}%"></i></div>'
        f'<div class="dagwrap">{diagram(view)}</div>'
        f'<table>{_phase_rows(view)}</table></section>'
    )


def render_page(views: list[RunView], *, title: str = "Pipelines") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if views:
        body = "".join(render_run(v) for v in views)
        total = sum(v.ledger.total_cost() for v in views)
        sub = f"{len(views)} run(s) · ${total:.2f} spent · generated {stamp}"
    else:
        body = ('<div class="empty">No runs found yet.<br/>'
                'Start one with <span class="mono">agent-pipeline start &lt;phase&gt;</span>.</div>')
        sub = f"generated {stamp}"

    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{e(title)}</title><style>{CSS}</style></head><body><div class="wrap">'
        f'<h1>{e(title)}</h1><div class="sub">{e(sub)}</div>{body}</div></body></html>'
    )

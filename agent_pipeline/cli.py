#!/usr/bin/env python3
"""agent-pipeline CLI.

Every verb either reports state or attempts a transition, and every transition
can be refused. Exit codes are meant to be used from scripts and hooks:

    0  allowed / clean
    1  refused — a gate said no
    2  the request itself was wrong (unknown phase, bad spec, missing file)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The installed package is self-contained: built-in blocks and checks ship
# inside it, so {engine} resolves here whether this was pip-installed into a
# venv or run straight out of a clone.
PACKAGE_ROOT = Path(__file__).resolve().parent

from agent_pipeline import (  # noqa: E402
    Context,
    Ledger,
    Verdict,
    check_can_complete,
    check_can_ship,
    check_can_start,
    load_blocks,
    load_pipeline,
    now_iso,
    render_report,
    render_status,
    run_pipeline,
)
from agent_pipeline.gates import GateError  # noqa: E402
from agent_pipeline.graph import render as render_graph  # noqa: E402
from agent_pipeline.notify import emit as emit_event  # noqa: E402
from agent_pipeline.report import build_view, find_ledgers, render_page  # noqa: E402
from agent_pipeline.spec import SpecError  # noqa: E402

BUILTIN_BLOCKS = PACKAGE_ROOT / "blocks"

EXIT_OK, EXIT_REFUSED, EXIT_BAD_REQUEST = 0, 1, 2


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------

def _find_pipeline(root: Path, explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        candidate = p if p.is_absolute() else root / p
        if not candidate.is_file():
            raise SpecError(f"no pipeline file at {candidate}")
        return candidate
    for guess in (root / "pipeline.yaml", root / "pipeline.yml"):
        if guess.is_file():
            return guess
    found = sorted((root / "pipelines").glob("*.yaml")) if (root / "pipelines").is_dir() else []
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        raise SpecError(
            f"{root / 'pipelines'} holds {len(found)} pipelines — name one with --pipeline: "
            f"{[f.name for f in found]}"
        )
    raise SpecError(
        f"no pipeline found under {root}. Expected ./pipeline.yaml or ./pipelines/*.yaml, "
        f"or pass --pipeline. Create one with: agent-pipeline init <name>"
    )


def _parse_vars(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SpecError(f"--var expects key=value, got {pair!r}")
        key, _, value = pair.partition("=")
        if not key.strip():
            raise SpecError(f"--var has an empty key: {pair!r}")
        out[key.strip()] = value
    return out


def _load(args: argparse.Namespace):
    root = Path(args.root).resolve()
    blocks = load_blocks(BUILTIN_BLOCKS, root / "blocks")
    path = _find_pipeline(root, args.pipeline)
    pipeline = load_pipeline(path, blocks)
    variables = _parse_vars(getattr(args, "var", None))
    variables.setdefault("run", args.run)
    variables.setdefault("root", str(root))
    try:
        workdir_rel = pipeline.workdir.format(**variables)
    except KeyError as exc:
        raise SpecError(
            f"{pipeline.source}: workdir {pipeline.workdir!r} needs {exc.args[0]!r}. "
            f"Pass it with --var {exc.args[0]}=..."
        ) from exc
    workdir = Path(workdir_rel)
    workdir = workdir if workdir.is_absolute() else root / workdir
    ctx = Context(root=root, run=args.run, workdir=workdir, engine=PACKAGE_ROOT, vars=variables)
    ledger = Ledger.load(workdir, pipeline.name, args.run)
    # Remember how this run was parameterised; a report generated later has
    # no other way to resolve {slug}-style artifact paths.
    ledger.vars = {**ledger.vars, **variables}
    return pipeline, ledger, ctx, blocks


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def _notify(args, pipeline, ctx, event: str, *, phase: str = "", detail: str = "") -> None:
    """Fire outbound hooks. Never lets a failed notification affect the run."""
    if getattr(args, "no_notify", False) or not pipeline.notify:
        return
    result = emit_event(
        pipeline.notify, event,
        pipeline=pipeline.name, run=ctx.run, root=ctx.root,
        phase=phase, detail=detail,
    )
    for label in result.fired:
        print(f"    → notified: {label}")
    for label, why in result.failed:
        # Loud, but not fatal. A broken webhook is worth seeing and not worth
        # stopping for.
        print(f"    → notify FAILED ({label}): {why}", file=sys.stderr)


def _approval_pending(pipeline, phase, report) -> bool:
    """Is this refusal specifically 'a person has not answered yet'?"""
    if not any(f.code == "criterion-unanswered" for f in report.failures):
        return False
    human = {c.id for c in phase.criteria if c.kind == "human"}
    return any(any(f"[{cid}]" in f.message for cid in human) for f in report.failures)


def cmd_validate(args: argparse.Namespace) -> int:
    pipeline, _, _, blocks = _load(args)
    print(f"\n  ✓ {pipeline.source}")
    print(f"    pipeline '{pipeline.name}' · mode={pipeline.mode} · {len(pipeline.phases)} phases")
    warnings = 0
    for p in pipeline.phases:
        deps = f" ← {', '.join(p.depends_on)}" if p.depends_on else ""
        opt = " (optional)" if p.optional else ""
        blk = f" [{p.block}]" if p.block else ""
        print(f"      {p.id}{blk}{opt}{deps}")
        for c in p.criteria:
            flag = "" if c.blocking else " advisory"
            times = f" ×{c.independence}" if c.independence > 1 else ""
            print(f"          · {c.id} ({c.kind}{times}){flag}")
        # A phase with neither an artifact nor a blocking criterion has nothing
        # the engine can evaluate — it would complete on the say-so of whoever
        # asked, which is the exact hole this whole engine exists to close.
        if p.artifact is None and not p.blocking_criteria() and not p.optional:
            warnings += 1
            print("          ! nothing verifiable: no artifact and no blocking criterion")
    print(f"\n    {len(blocks)} blocks available: {', '.join(sorted(blocks))}")
    if warnings:
        print(f"    {warnings} phase(s) have nothing the engine can check.")
    print()
    return EXIT_OK


def cmd_blocks(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    blocks = load_blocks(BUILTIN_BLOCKS, root / "blocks")
    print()
    for bid in sorted(blocks):
        b = blocks[bid]
        where = "built-in" if b.source and BUILTIN_BLOCKS in b.source.parents else "project"
        print(f"  {bid:<14} {b.name:<26} [{where}]")
        print(f"                 {b.description.strip().splitlines()[0][:88]}")
        if b.criteria:
            print(f"                 criteria: {', '.join(c.id for c in b.criteria)}")
        print()
    return EXIT_OK


def cmd_guide(args: argparse.Namespace) -> int:
    """Print what the worker of this phase needs to know, and what will be checked.

    This is the handoff surface for an agent: guidance plus the exact list of
    conditions that will be evaluated, before any work starts.
    """
    pipeline, ledger, ctx, _ = _load(args)
    phase = pipeline.phase(args.phase)
    artifact = ctx.artifact_path(phase)
    print(f"\n  {phase.id} — {phase.name}")
    if phase.block:
        print(f"  block: {phase.block}")
    if artifact:
        print(f"  artifact: {artifact}")
    if phase.depends_on:
        print(f"  depends on: {', '.join(phase.depends_on)}")
    if phase.context:
        print("\n  Required reading — read every one of these BEFORE starting:")
        for path in ctx.context_paths(phase):
            mark = "" if path.is_file() else "   ← MISSING (start will refuse)"
            print(f"    · {path}{mark}")
    if phase.guidance:
        print("\n" + "\n".join(f"  {line}" for line in phase.guidance.splitlines()))
    if phase.criteria:
        print("\n  This phase will be judged against:")
        for c in phase.criteria:
            mark = "" if c.blocking else "  (advisory)"
            panel = f" — requires {c.independence} independent verdicts" if c.independence > 1 else ""
            print(f"    [{c.kind}] {c.id}{mark}{panel}")
            print(f"        {c.description}")
            if c.kind == "mechanical" and c.run:
                print(f"        engine runs: {c.run}")
            if c.ask:
                print(f"        question: {c.ask}")
    print()
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    pipeline, ledger, ctx, _ = _load(args)
    print(render_status(pipeline, ledger, ctx, verbose=args.verbose))
    return EXIT_OK


def cmd_start(args: argparse.Namespace) -> int:
    pipeline, ledger, ctx, _ = _load(args)
    phase = pipeline.phase(args.phase)
    report = check_can_start(pipeline, ledger, phase, ctx)
    if not report.ok:
        print(render_report(report, title=f"cannot start '{phase.id}'"))
        return EXIT_REFUSED
    entry = ledger.entry(phase.id)
    if entry.status == "complete":
        print(f"  '{phase.id}' is already complete. Use `reopen {phase.id}` to work on it again.")
        return EXIT_REFUSED
    entry.status = "active"
    entry.started_at = entry.started_at or now_iso()
    ledger.save(ctx.workdir)
    print(f"  ◐ started {phase.id} ({phase.name})")
    _notify(args, pipeline, ctx, "phase_started", phase=phase.id, detail=phase.name)
    artifact = ctx.artifact_path(phase)
    if artifact:
        print(f"    expected artifact: {artifact}")
    if phase.blocking_criteria():
        print(f"    will be checked against: {', '.join(c.id for c in phase.blocking_criteria())}")
    return EXIT_OK


def cmd_complete(args: argparse.Namespace) -> int:
    pipeline, ledger, ctx, _ = _load(args)
    phase = pipeline.phase(args.phase)
    report = check_can_complete(pipeline, ledger, phase, ctx)
    ledger.save(ctx.workdir)

    if not report.ok and not args.force:
        print(render_report(report, title=f"cannot complete '{phase.id}'"))
        blockers = "\n".join(f"• {f.message}" for f in report.failures)
        event = "approval_needed" if _approval_pending(pipeline, phase, report) else "phase_blocked"
        _notify(args, pipeline, ctx, event, phase=phase.id, detail=blockers)
        return EXIT_REFUSED

    entry = ledger.entry(phase.id)
    if not report.ok and args.force:
        if not args.reason:
            print("  --force requires --reason. Forcing without a recorded reason is how a "
                  "skipped gate becomes invisible later.")
            return EXIT_BAD_REQUEST
        entry.forced = True
        entry.notes.append(f"FORCED {now_iso()}: {args.reason}")

    entry.status = "complete"
    entry.completed_at = now_iso()
    artifact = ctx.artifact_path(phase)
    entry.artifact = str(artifact) if artifact else None
    ledger.save(ctx.workdir)
    mark = "⚠ FORCED" if entry.forced else "✓"
    print(f"  {mark} completed {phase.id} ({phase.name})")
    if entry.forced:
        print("    This will be flagged at ship time.")
    _notify(args, pipeline, ctx, "phase_completed", phase=phase.id, detail=phase.name)
    return EXIT_OK


def _record(args: argparse.Namespace, kind: str, by: str) -> int:
    pipeline, ledger, ctx, _ = _load(args)
    phase = pipeline.phase(args.phase)
    match = [c for c in phase.criteria if c.id == args.criterion]
    if not match:
        print(f"  phase '{phase.id}' has no criterion '{args.criterion}'. "
              f"Known: {[c.id for c in phase.criteria]}")
        return EXIT_BAD_REQUEST
    criterion = match[0]
    if criterion.kind != kind:
        verb = {"judged": "judge", "human": "approve", "mechanical": "(engine-run)"}[criterion.kind]
        print(f"  '{criterion.id}' is a {criterion.kind} criterion — use `{verb}` instead. "
              f"Mechanical criteria are run by the engine and cannot be asserted.")
        return EXIT_BAD_REQUEST

    evidence = args.evidence or args.note or ""
    if args.status == "pass" and not evidence.strip():
        print("  a passing verdict needs --evidence. 'It looks fine' is not a record.")
        return EXIT_BAD_REQUEST

    author = args.by or by
    ledger.record_verdict(
        phase.id,
        Verdict(
            criterion=criterion.id,
            kind=criterion.kind,
            status=args.status,
            evidence=evidence,
            recorded_at=now_iso(),
            by=author,
        ),
    )
    ledger.save(ctx.workdir)
    mark = "✓" if args.status == "pass" else "✗"
    tally = ""
    if criterion.independence > 1:
        passes = [
            v for v in ledger.entry(phase.id).current_panel(criterion.id, criterion.kind)
            if v.passed()
        ]
        tally = f"  ({len(passes)}/{criterion.independence} independent)"
        if not args.by:
            print(f"  note: '{criterion.id}' needs {criterion.independence} DISTINCT judges — "
                  f"pass --by <name>; a repeated author replaces their own verdict, it does not add.")
    print(f"  {mark} recorded {args.status} on {phase.id}/{criterion.id} by {author}{tally}")
    return EXIT_OK


def cmd_judge(args: argparse.Namespace) -> int:
    return _record(args, "judged", by="judge")


def cmd_approve(args: argparse.Namespace) -> int:
    return _record(args, "human", by="human")


def cmd_cost(args: argparse.Namespace) -> int:
    pipeline, ledger, ctx, _ = _load(args)
    phase = pipeline.phase(args.phase)
    entry = ledger.entry(phase.id)
    entry.cost_usd = round(entry.cost_usd + args.usd, 4)
    ledger.save(ctx.workdir)
    print(f"  logged ${args.usd:.4f} on {phase.id} (phase total ${entry.cost_usd:.4f}, "
          f"run total ${ledger.total_cost():.4f})")
    return EXIT_OK


def cmd_reopen(args: argparse.Namespace) -> int:
    """Invalidate a phase and only what genuinely depends on it.

    The dependency graph is what makes this surgical. Without edges the only
    safe move is to invalidate everything downstream by position, which throws
    away work that was never affected.
    """
    pipeline, ledger, ctx, _ = _load(args)
    phase = pipeline.phase(args.phase)
    if not args.reason:
        print("  reopen requires --reason — the reason is the only record of why a "
              "completed phase stopped counting.")
        return EXIT_BAD_REQUEST

    affected = [phase.id, *pipeline.dependents_of(phase.id)]
    reopened: list[str] = []
    for pid in affected:
        entry = ledger.phases.get(pid)
        if entry is None or entry.status == "pending":
            continue
        ledger.snapshot(pid, reason=args.reason)
        entry.status = "pending"
        entry.completed_at = None
        entry.started_at = None
        entry.forced = False
        entry.verdicts = {}
        entry.iteration += 1
        reopened.append(pid)

    ledger.iteration += 1
    ledger.save(ctx.workdir)

    untouched = [p.id for p in pipeline.phases if p.id not in affected and ledger.is_complete(p.id)]
    print(f"  reopened: {', '.join(reopened) if reopened else '(nothing was complete)'}")
    if untouched:
        print(f"  untouched (not downstream): {', '.join(untouched)}")
    print(f"  reason: {args.reason}")
    return EXIT_OK


def cmd_ship(args: argparse.Namespace) -> int:
    pipeline, ledger, ctx, _ = _load(args)
    report = check_can_ship(pipeline, ledger, ctx)
    if args.allow_forced:
        report.failures = [f for f in report.failures if f.code != "phase-forced"]
        report.ok = not report.failures
    if not report.ok:
        print(render_report(report, title=f"'{pipeline.name}' is NOT shippable"))
        return EXIT_REFUSED
    print(f"\n  ✓ {pipeline.name} run={ledger.run} — all required gates passed "
          f"(${ledger.total_cost():.2f} spent)\n")
    _notify(args, pipeline, ctx, "run_shipped",
            detail=f"${ledger.total_cost():.2f} spent across {len(pipeline.phases)} phases")
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    pipeline, ledger, ctx, _ = _load(args)

    def on_event(kind: str, phase, report) -> None:
        symbol = {"start": "◐", "complete": "✓", "cached": "·",
                  "blocked": "⛔", "absorbed": "↩"}[kind]
        note = {"cached": " (cached)",
                "absorbed": " (verdict recorded by the phase itself)"}.get(kind, "")
        print(f"  {symbol} {phase.id}{note}", flush=True)
        if kind == "absorbed":
            return
        if kind == "start":
            _notify(args, pipeline, ctx, "phase_started", phase=phase.id, detail=phase.name)
        elif kind == "complete":
            _notify(args, pipeline, ctx, "phase_completed", phase=phase.id, detail=phase.name)
        elif kind == "blocked" and report is not None:
            blockers = "\n".join(f"• {f.message}" for f in report.failures)
            event = "approval_needed" if _approval_pending(pipeline, phase, report) else "phase_blocked"
            _notify(args, pipeline, ctx, event, phase=phase.id, detail=blockers)

    result = run_pipeline(
        pipeline, ledger, ctx,
        start_from=args.start_from, only=args.only, timeout=args.timeout,
        on_event=on_event,
    )
    if not result.ok:
        blocked = next((s for s in result.steps if s.phase == result.stopped_at), None)
        if blocked and blocked.report:
            print(render_report(blocked.report, title=f"run stopped at '{result.stopped_at}'"))
        print(f"  completed: {result.completed or '(none)'}  ·  cached: {result.skipped or '(none)'}")
        return EXIT_REFUSED
    print(f"\n  ✓ run complete — {len(result.completed)} phases ran, "
          f"{len(result.skipped)} served from the ledger\n")
    return EXIT_OK


def cmd_graph(args: argparse.Namespace) -> int:
    """Draw the pipeline — structure by default, live run state with --status.

    Emitted from the same YAML the engine executes, so a diagram pasted into a
    README cannot drift from the pipeline it claims to describe.
    """
    pipeline, ledger, ctx, _ = _load(args)
    text = render_graph(
        pipeline,
        ledger if args.status else None,
        ctx if args.status else None,
        fmt=args.format,
        direction=args.direction,
    )
    if args.fence and args.format == "mermaid":
        text = f"```mermaid\n{text}\n```"
    if args.out:
        out = Path(args.out)
        out = out if out.is_absolute() else Path(args.root).resolve() / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"  wrote {out}")
        return EXIT_OK
    print(text)
    return EXIT_OK


def cmd_report(args: argparse.Namespace) -> int:
    """One self-contained HTML page showing every run found under --root.

    Deliberately a file rather than a server: the person who most needs to see
    where a pipeline is stuck is usually the one who will not run a command to
    find out, and a dashboard that needs a dev server is one nobody opens.
    """
    root = Path(args.root).resolve()
    blocks = load_blocks(BUILTIN_BLOCKS, root / "blocks")

    # Index every pipeline definition we can find, so a ledger can be matched
    # to the pipeline that produced it by name.
    candidates: list[Path] = []
    for guess in (root / "pipeline.yaml", root / "pipeline.yml"):
        if guess.is_file():
            candidates.append(guess)
    if (root / "pipelines").is_dir():
        candidates.extend(sorted((root / "pipelines").glob("*.y*ml")))
    if args.pipeline:
        explicit = Path(args.pipeline)
        candidates.append(explicit if explicit.is_absolute() else root / explicit)

    by_name = {}
    for path in candidates:
        try:
            pipe = load_pipeline(path, blocks)
        except SpecError as exc:
            print(f"  skipping {path.name}: {exc}", file=sys.stderr)
            continue
        by_name[pipe.name] = pipe

    if not by_name:
        raise SpecError(
            f"no pipeline definitions found under {root} — expected ./pipeline.yaml "
            f"or ./pipelines/*.yaml"
        )

    views = []
    for ledger_path in find_ledgers(root):
        workdir = ledger_path.parent.parent
        try:
            raw = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  skipping {ledger_path}: {exc}", file=sys.stderr)
            continue
        name = raw.get("pipeline", "")
        if name not in by_name:
            print(f"  skipping {ledger_path}: no pipeline named {name!r} found", file=sys.stderr)
            continue
        pipe = by_name[name]
        ledger = Ledger.load(workdir, name, raw.get("run", "main"))
        variables = {"run": ledger.run, "root": str(root), **ledger.vars}
        ctx = Context(root=root, run=ledger.run, workdir=workdir,
                      engine=PACKAGE_ROOT, vars=variables)
        views.append(build_view(pipe, ledger, ctx))

    views.sort(key=lambda v: (v.pipeline.name, v.ledger.run))
    out = Path(args.out) if args.out else root / "pipeline-report.html"
    out = out if out.is_absolute() else root / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_page(views, title=args.title), encoding="utf-8")
    print(f"  wrote {out}  ({len(views)} run(s))")

    if args.open:
        import webbrowser
        webbrowser.open(out.as_uri())
    return EXIT_OK


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    target = root / "pipelines" / f"{args.name}.yaml"
    if target.exists() and not args.force:
        print(f"  {target} already exists. Pass --force to overwrite.")
        return EXIT_BAD_REQUEST
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_STARTER.format(name=args.name), encoding="utf-8")
    print(f"  wrote {target}")
    print(f"  next: agent-pipeline validate --pipeline pipelines/{args.name}.yaml")
    return EXIT_OK


_STARTER = """\
# {name} — see docs/02-writing-a-pipeline.md
name: {name}
description: One line on what this pipeline produces.
mode: referee            # referee = an agent walks it · runner = the engine drives
workdir: runs/{{run}}

phases:
  - id: research
    block: research
    artifact: runs/{{run}}/research.md

  - id: draft
    block: creative
    artifact: runs/{{run}}/draft.md
    depends_on: [research]
    guidance: |
      Say here what "good" looks like for THIS pipeline.
      The block supplies the general craft rules; this is the specific brief.
    criteria:
      - id: on-topic
        kind: judged
        description: The draft answers the brief rather than an adjacent question.
        ask: "Does this draft answer the brief as written? PASS or FAIL, quote the line that decides it."

  - id: publish
    artifact: runs/{{run}}/published.md
    depends_on: [draft]
"""


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agent-pipeline",
        description="A flexible pipeline engine for any task you need.",
    )
    from agent_pipeline._version import __version__
    p.add_argument("--version", action="version", version=f"agent-pipeline {__version__}")
    p.add_argument("--root", default=".", help="project root (default: cwd)")
    p.add_argument("--pipeline", help="pipeline YAML (default: ./pipeline.yaml or ./pipelines/*.yaml)")
    p.add_argument("--run", default="main", help="run id — one ledger per run (default: main)")
    p.add_argument("--var", action="append", help="template variable, key=value (repeatable)")
    p.add_argument("--no-notify", action="store_true", help="suppress outbound notify hooks")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name: str, fn, help_: str):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(func=fn)
        return sp

    add("validate", cmd_validate, "load and check the pipeline definition — spends nothing")
    add("blocks", cmd_blocks, "list available blocks")

    sp = add("init", cmd_init, "scaffold a new pipeline")
    sp.add_argument("name")
    sp.add_argument("--force", action="store_true")

    sp = add("guide", cmd_guide, "print what a phase expects and how it will be judged")
    sp.add_argument("phase")

    sp = add("status", cmd_status, "show the run")
    sp.add_argument("-v", "--verbose", action="store_true", help="include every criterion verdict")

    sp = add("start", cmd_start, "open a phase")
    sp.add_argument("phase")

    sp = add("complete", cmd_complete, "close a phase — runs its gates")
    sp.add_argument("phase")
    sp.add_argument("--force", action="store_true", help="close despite failing gates (recorded)")
    sp.add_argument("--reason", help="required with --force")

    sp = add("judge", cmd_judge, "record a verdict on a judged criterion")
    sp.add_argument("phase")
    sp.add_argument("criterion")
    sp.add_argument("--status", choices=["pass", "fail"], required=True)
    sp.add_argument("--evidence", help="what makes this true — required to pass")
    sp.add_argument("--note")
    sp.add_argument("--by", help="who judged (default: judge)")

    sp = add("approve", cmd_approve, "record a human decision on a human criterion")
    sp.add_argument("phase")
    sp.add_argument("criterion")
    sp.add_argument("--status", choices=["pass", "fail"], default="pass")
    sp.add_argument("--note", help="the correction, or why it was approved")
    sp.add_argument("--evidence")
    sp.add_argument("--by", help="who approved (default: human)")

    sp = add("cost", cmd_cost, "log spend against a phase")
    sp.add_argument("phase")
    sp.add_argument("--usd", type=float, required=True)

    sp = add("reopen", cmd_reopen, "invalidate a phase and only its real dependents")
    sp.add_argument("phase")
    sp.add_argument("--reason")

    sp = add("ship", cmd_ship, "the final refusal — everything required, nothing stale")
    sp.add_argument("--allow-forced", action="store_true")

    sp = add("report", cmd_report, "write one self-contained HTML page for every run")
    sp.add_argument("--out", help="output path (default: <root>/pipeline-report.html)")
    sp.add_argument("--title", default="Pipelines", help="page heading")
    sp.add_argument("--open", action="store_true", help="open it in a browser")

    sp = add("graph", cmd_graph, "draw the pipeline (mermaid / dot)")
    sp.add_argument("--status", action="store_true", help="colour by live run state")
    sp.add_argument("--format", choices=["mermaid", "dot"], default="mermaid")
    sp.add_argument("--direction", default="LR", help="mermaid direction: LR, TD, RL, BT")
    sp.add_argument("--fence", action="store_true", help="wrap in a ```mermaid fence for markdown")
    sp.add_argument("--out", help="write to a file instead of stdout")

    sp = add("run", cmd_run, "runner mode — the engine drives the loop")
    sp.add_argument("--start-from", help="begin at this phase")
    sp.add_argument("--only", help="run exactly one phase")
    sp.add_argument("--timeout", type=int, default=3600, help="per-phase seconds (default: 3600)")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (SpecError, GateError) as exc:
        print(f"\n  ⛔ {exc}\n", file=sys.stderr)
        return EXIT_BAD_REQUEST


if __name__ == "__main__":
    raise SystemExit(main())

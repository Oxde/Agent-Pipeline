"""Outbound events — tell someone when something happened.

An agent talking to a person over Telegram, Slack or email needs to say "the
draft is done" and, more importantly, "I need you before I can continue". That
is what this is: the pipeline declares commands to run on lifecycle events, and
the engine fires them.

Two rules, both load-bearing:

**A notification can never break the pipeline.** Every hook runs detached from
the outcome — a dead webhook, a bad token, a missing script is logged and
ignored. Work does not stop because a message failed to send.

**The payload arrives as environment variables, not as shell arguments.** Phase
names and blocker text are data, and interpolating data into a command line is
how a message body containing a quote becomes a shell injection. Only the safe
identifier fields are available as `{}` templates.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Event = Literal[
    "phase_started",
    "phase_completed",
    "phase_blocked",
    "approval_needed",
    "run_shipped",
]

EVENTS: tuple[Event, ...] = (
    "phase_started",
    "phase_completed",
    "phase_blocked",
    "approval_needed",
    "run_shipped",
)

# Human-readable defaults, so a hook that just forwards $AGENT_PIPELINE_MESSAGE
# sends something a person can read without writing any formatting code.
HEADLINE: dict[str, str] = {
    "phase_started": "▶ started {phase}",
    "phase_completed": "✓ finished {phase}",
    "phase_blocked": "⛔ blocked at {phase}",
    "approval_needed": "✋ needs you: {phase}",
    "run_shipped": "🚢 {pipeline} shipped",
}

NOTIFY_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class Hook:
    """One command to run when one of `on` happens."""

    on: tuple[str, ...]
    run: str
    name: str = ""

    def matches(self, event: str) -> bool:
        return event in self.on


@dataclass
class Dispatch:
    """What was attempted, so `--verbose` and tests can see it."""

    event: str
    fired: list[str]
    failed: list[tuple[str, str]]


def build_message(
    event: str,
    *,
    pipeline: str,
    run: str,
    phase: str = "",
    detail: str = "",
) -> str:
    head = HEADLINE.get(event, event).format(phase=phase or "-", pipeline=pipeline)
    parts = [f"[{pipeline} · {run}] {head}"]
    if detail.strip():
        parts.append(detail.strip())
    return "\n".join(parts)


def emit(
    hooks: list[Hook],
    event: str,
    *,
    pipeline: str,
    run: str,
    root: Path,
    phase: str = "",
    detail: str = "",
    dry_run: bool = False,
) -> Dispatch:
    """Fire every hook registered for `event`. Never raises."""
    result = Dispatch(event=event, fired=[], failed=[])
    if not hooks:
        return result

    message = build_message(event, pipeline=pipeline, run=run, phase=phase, detail=detail)
    env = {
        **os.environ,
        "AGENT_PIPELINE_EVENT": event,
        "AGENT_PIPELINE_PIPELINE": pipeline,
        "AGENT_PIPELINE_RUN": run,
        "AGENT_PIPELINE_PHASE": phase,
        "AGENT_PIPELINE_DETAIL": detail,
        "AGENT_PIPELINE_MESSAGE": message,
    }

    for hook in hooks:
        if not hook.matches(event):
            continue
        label = hook.name or hook.run.split()[0]
        # Only identifiers are templated. Free text (the message, the blocker
        # detail) is reachable solely through the environment, so a phase name
        # is the widest thing that can ever reach a command line.
        try:
            command = hook.run.format(event=event, phase=phase, run=run, pipeline=pipeline)
        except KeyError as exc:
            result.failed.append((label, f"unknown template field {exc.args[0]!r} — "
                                         f"only event, phase, run, pipeline are available"))
            continue

        if dry_run:
            result.fired.append(f"{label} (dry-run): {command}")
            continue

        try:
            proc = subprocess.run(
                shlex.split(command),
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=NOTIFY_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            result.failed.append((label, f"command not found ({exc.strerror})"))
            continue
        except subprocess.TimeoutExpired:
            result.failed.append((label, f"timed out after {NOTIFY_TIMEOUT_SECONDS}s"))
            continue
        except OSError as exc:
            result.failed.append((label, str(exc)))
            continue

        if proc.returncode == 0:
            result.fired.append(label)
        else:
            tail = ((proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}")
            result.failed.append((label, tail[:200]))

    return result

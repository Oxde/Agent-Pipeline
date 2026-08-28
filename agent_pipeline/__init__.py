"""agent-pipeline — a flexible pipeline engine for any task you need.

The engine is domain-agnostic and stays that way. Everything about *your* work
lives in a pipeline YAML and in blocks; nothing about it belongs in here.

    spec     load and validate pipelines + blocks (no work, no spend)
    ledger   run state — phase entries and recorded verdicts
    gates    the referee: what may open, what may close, what is stale
    runner   runner mode, where the engine owns the loop
    status   the one plain-text view of a run
"""

from .gates import Context, GateReport, check_can_complete, check_can_ship, check_can_start
from .ledger import Ledger, PhaseEntry, Verdict, now_iso
from .runner import RunResult, run_pipeline
from .spec import Block, Criterion, Phase, Pipeline, SpecError, load_blocks, load_pipeline
from .status import render_report, render_status

from ._version import __version__  # noqa: F401

__all__ = [
    "Block",
    "Context",
    "Criterion",
    "GateReport",
    "Ledger",
    "Phase",
    "PhaseEntry",
    "Pipeline",
    "RunResult",
    "SpecError",
    "Verdict",
    "check_can_complete",
    "check_can_ship",
    "check_can_start",
    "load_blocks",
    "load_pipeline",
    "now_iso",
    "render_report",
    "render_status",
    "run_pipeline",
    "__version__",
]

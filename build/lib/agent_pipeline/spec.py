"""Load and validate pipeline + block definitions.

A pipeline is data, never code. This module turns two kinds of YAML —
``blocks/*.yaml`` (reusable phase types) and a pipeline file (the actual
sequence) — into frozen dataclasses, and refuses anything malformed with an
error that names the file, the key and what was expected.

Nothing here executes work. Resolution happens before a single token is spent,
which is the point: ``agent-pipeline validate`` is meant to catch a broken
pipeline for free rather than three paid steps in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

import yaml

from .notify import EVENTS, Hook

CriterionKind = Literal["mechanical", "judged", "human"]
RunMode = Literal["referee", "runner"]

VALID_KINDS: tuple[CriterionKind, ...] = ("mechanical", "judged", "human")
VALID_MODES: tuple[RunMode, ...] = ("referee", "runner")

ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class SpecError(Exception):
    """A pipeline or block definition is malformed.

    Always raised with the source file and the offending key — a spec error the
    author can't locate is worse than no validation at all.
    """


# ---------------------------------------------------------------------------
# small typed readers — every one of these fails loudly rather than defaulting
# ---------------------------------------------------------------------------

def _require(mapping: dict[str, object], key: str, where: str) -> object:
    if key not in mapping:
        raise SpecError(f"{where}: missing required key '{key}'")
    return mapping[key]


def _as_str(value: object, key: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SpecError(f"{where}: '{key}' must be a non-empty string, got {value!r}")
    return value


def _as_bool(value: object, key: str, where: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise SpecError(f"{where}: '{key}' must be true or false, got {value!r}")
    return value


def _as_int(value: object, key: str, where: str, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SpecError(f"{where}: '{key}' must be a non-negative integer, got {value!r}")
    return value


def _as_str_list(value: object, key: str, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SpecError(f"{where}: '{key}' must be a list, got {value!r}")
    out: list[str] = []
    for i, item in enumerate(value):
        out.append(_as_str(item, f"{key}[{i}]", where))
    return out


def _as_mapping(value: object, key: str, where: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SpecError(f"{where}: '{key}' must be a mapping, got {value!r}")
    return {str(k): v for k, v in value.items()}


def _check_id(value: str, key: str, where: str) -> str:
    if not ID_RE.match(value):
        raise SpecError(
            f"{where}: '{key}' must be lowercase alphanumeric with . _ - "
            f"(it becomes a filename and a CLI argument), got {value!r}"
        )
    return value


def _load_yaml(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise SpecError(f"no such file: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SpecError(f"{path}: invalid YAML — {exc}") from exc
    if raw is None:
        raise SpecError(f"{path}: file is empty")
    if not isinstance(raw, dict):
        raise SpecError(f"{path}: top level must be a mapping, got {type(raw).__name__}")
    return {str(k): v for k, v in raw.items()}


# ---------------------------------------------------------------------------
# criteria
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Criterion:
    """One checkable condition on a phase's artifact.

    The ``kind`` decides who is allowed to satisfy it, and that distinction is
    the whole anti-skipping mechanism:

    ``mechanical``  a command. The engine runs it itself at completion time and
                    does not care what anyone claimed.
    ``judged``      a question a model answers. The engine cannot run it, so it
                    demands a *recorded* verdict with evidence before the phase
                    may close. An unanswered judged criterion blocks.
    ``human``       a decision only a person may record.
    """

    id: str
    kind: CriterionKind
    description: str
    run: str | None = None
    ask: str | None = None
    blocking: bool = True

    @staticmethod
    def parse(raw: object, where: str) -> "Criterion":
        if not isinstance(raw, dict):
            raise SpecError(f"{where}: each criterion must be a mapping, got {raw!r}")
        data = {str(k): v for k, v in raw.items()}
        cid = _check_id(_as_str(_require(data, "id", where), "id", where), "id", where)
        seat = f"{where} criterion '{cid}'"
        kind = _as_str(_require(data, "kind", seat), "kind", seat)
        if kind not in VALID_KINDS:
            raise SpecError(f"{seat}: 'kind' must be one of {list(VALID_KINDS)}, got {kind!r}")
        description = _as_str(_require(data, "description", seat), "description", seat)
        run = data.get("run")
        ask = data.get("ask")

        if kind == "mechanical":
            if run is None:
                raise SpecError(f"{seat}: kind 'mechanical' requires 'run' (the command to execute)")
            run = _as_str(run, "run", seat)
            if ask is not None:
                raise SpecError(f"{seat}: kind 'mechanical' must not set 'ask'")
        else:
            if run is not None:
                raise SpecError(f"{seat}: kind '{kind}' must not set 'run' — only 'mechanical' runs commands")
            run = None

        if kind == "judged":
            if ask is None:
                raise SpecError(f"{seat}: kind 'judged' requires 'ask' (the question the judge answers)")
            ask = _as_str(ask, "ask", seat)
        elif kind == "human":
            ask = _as_str(ask, "ask", seat) if ask is not None else None
        else:
            ask = None

        return Criterion(
            id=cid,
            kind=kind,  # type: ignore[arg-type]
            description=description,
            run=run,
            ask=ask,
            blocking=_as_bool(data.get("blocking"), "blocking", seat, True),
        )


def _parse_criteria(raw: object, where: str) -> list[Criterion]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SpecError(f"{where}: 'criteria' must be a list, got {raw!r}")
    parsed = [Criterion.parse(item, where) for item in raw]
    seen: set[str] = set()
    for c in parsed:
        if c.id in seen:
            raise SpecError(f"{where}: duplicate criterion id '{c.id}'")
        seen.add(c.id)
    return parsed


# ---------------------------------------------------------------------------
# blocks
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Block:
    """A reusable phase type.

    A block carries the parts of a step that are the same everywhere it appears:
    what the output looks like, what has to be true of it, whether it may
    iterate, and the guidance handed to whoever does the work.
    """

    id: str
    name: str
    description: str
    guidance: str = ""
    artifact: str | None = None
    criteria: list[Criterion] = field(default_factory=list)
    iterate: bool = False
    max_iterations: int = 0
    archive: bool = False
    cost_aware: bool = False
    extends: str | None = None
    source: Path | None = None

    @staticmethod
    def parse(path: Path) -> "Block":
        data = _load_yaml(path)
        where = str(path)
        bid = _check_id(_as_str(_require(data, "id", where), "id", where), "id", where)
        seat = f"{where} (block '{bid}')"
        iterate = _as_bool(data.get("iterate"), "iterate", seat, False)
        max_iterations = _as_int(data.get("max_iterations"), "max_iterations", seat, 5 if iterate else 0)
        if iterate and max_iterations == 0:
            raise SpecError(f"{seat}: 'iterate: true' needs a non-zero 'max_iterations'")
        artifact = data.get("artifact")
        return Block(
            id=bid,
            name=_as_str(_require(data, "name", seat), "name", seat),
            description=_as_str(_require(data, "description", seat), "description", seat),
            guidance=str(data.get("guidance") or "").strip(),
            artifact=_as_str(artifact, "artifact", seat) if artifact is not None else None,
            criteria=_parse_criteria(data.get("criteria"), seat),
            iterate=iterate,
            max_iterations=max_iterations,
            archive=_as_bool(data.get("archive"), "archive", seat, iterate),
            cost_aware=_as_bool(data.get("cost_aware"), "cost_aware", seat, False),
            extends=_as_str(data["extends"], "extends", seat) if data.get("extends") else None,
            source=path,
        )


def load_blocks(*dirs: Path) -> dict[str, Block]:
    """Load every ``*.yaml`` in each directory, later dirs overriding earlier.

    That ordering is what lets a project keep its own ``blocks/`` next to the
    built-in ones and shadow a built-in by reusing its id.
    """
    blocks: dict[str, Block] = {}
    for d in dirs:
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.yaml")):
            block = Block.parse(path)
            blocks[block.id] = block
    return _resolve_inheritance(blocks)


def _resolve_inheritance(blocks: dict[str, Block]) -> dict[str, Block]:
    resolved: dict[str, Block] = {}

    def resolve(bid: str, trail: tuple[str, ...]) -> Block:
        if bid in resolved:
            return resolved[bid]
        if bid in trail:
            raise SpecError(f"block inheritance cycle: {' -> '.join((*trail, bid))}")
        block = blocks[bid]
        if block.extends is None:
            resolved[bid] = block
            return block
        if block.extends not in blocks:
            raise SpecError(
                f"{block.source}: block '{bid}' extends '{block.extends}', which is not a known block. "
                f"Known: {sorted(blocks)}"
            )
        parent = resolve(block.extends, (*trail, bid))
        merged = replace(
            block,
            guidance=block.guidance or parent.guidance,
            artifact=block.artifact if block.artifact is not None else parent.artifact,
            criteria=_merge_criteria(parent.criteria, block.criteria),
        )
        resolved[bid] = merged
        return merged

    for bid in blocks:
        resolve(bid, ())
    return resolved


def _merge_criteria(parent: list[Criterion], child: list[Criterion]) -> list[Criterion]:
    """Child criteria override parent criteria of the same id; the rest append."""
    by_id = {c.id: c for c in parent}
    order = [c.id for c in parent]
    for c in child:
        if c.id not in by_id:
            order.append(c.id)
        by_id[c.id] = c
    return [by_id[i] for i in order]


# ---------------------------------------------------------------------------
# phases + pipeline
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Phase:
    """One step of a pipeline, after its block has been folded in."""

    id: str
    name: str
    block: str | None
    artifact: str | None
    depends_on: list[str]
    criteria: list[Criterion]
    guidance: str
    optional: bool
    iterate: bool
    max_iterations: int
    archive: bool
    cost_aware: bool
    # Only consulted in runner mode: the command that performs this phase.
    # In referee mode an agent does the work and this stays None.
    run: str | None = None

    def blocking_criteria(self) -> list[Criterion]:
        return [c for c in self.criteria if c.blocking]


@dataclass(frozen=True)
class Pipeline:
    """A whole pipeline: an ordered, dependency-checked list of phases."""

    name: str
    description: str
    mode: RunMode
    workdir: str
    phases: list[Phase]
    notify: list[Hook] = field(default_factory=list)
    source: Path | None = None

    def phase(self, phase_id: str) -> Phase:
        for p in self.phases:
            if p.id == phase_id:
                return p
        raise SpecError(
            f"pipeline '{self.name}' has no phase '{phase_id}'. Known: {[p.id for p in self.phases]}"
        )

    def index(self, phase_id: str) -> int:
        for i, p in enumerate(self.phases):
            if p.id == phase_id:
                return i
        raise SpecError(f"pipeline '{self.name}' has no phase '{phase_id}'")

    def dependents_of(self, phase_id: str) -> list[str]:
        """Every phase that transitively depends on ``phase_id``.

        This is what makes reopening surgical instead of scorched-earth: only
        the phases genuinely downstream of a change are invalidated, so editing
        one still does not throw away the voiceover.
        """
        out: list[str] = []
        frontier = {phase_id}
        for p in self.phases:
            if p.id == phase_id:
                continue
            if frontier.intersection(p.depends_on):
                out.append(p.id)
                frontier.add(p.id)
        return out


def load_pipeline(path: Path, blocks: dict[str, Block]) -> Pipeline:
    data = _load_yaml(path)
    where = str(path)
    name = _check_id(_as_str(_require(data, "name", where), "name", where), "name", where)
    seat = f"{where} (pipeline '{name}')"

    mode = str(data.get("mode") or "referee")
    if mode not in VALID_MODES:
        raise SpecError(f"{seat}: 'mode' must be one of {list(VALID_MODES)}, got {mode!r}")

    raw_phases = _require(data, "phases", seat)
    if not isinstance(raw_phases, list) or not raw_phases:
        raise SpecError(f"{seat}: 'phases' must be a non-empty list")

    phases: list[Phase] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_phases):
        phase = _parse_phase(raw, f"{seat} phases[{i}]", blocks)
        if phase.id in seen:
            raise SpecError(f"{seat}: duplicate phase id '{phase.id}'")
        seen.add(phase.id)
        phases.append(phase)

    _check_dependencies(phases, seat)

    return Pipeline(
        name=name,
        description=str(data.get("description") or "").strip(),
        mode=mode,  # type: ignore[arg-type]
        workdir=_as_str(data.get("workdir") or "runs/{run}", "workdir", seat),
        phases=phases,
        notify=_parse_notify(data.get("notify"), seat),
        source=path,
    )


def _parse_notify(raw: object, where: str) -> list[Hook]:
    """Outbound hooks: what to run when something happens.

    Kept deliberately dumb — a list of (events, command). Anything clever about
    formatting or delivery belongs in the command, not in the engine.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SpecError(f"{where}: 'notify' must be a list, got {raw!r}")
    hooks: list[Hook] = []
    for i, item in enumerate(raw):
        seat = f"{where} notify[{i}]"
        if not isinstance(item, dict):
            raise SpecError(f"{seat}: each notify entry must be a mapping, got {item!r}")
        # YAML 1.1 reads a bare `on:` as the boolean True, so the key arrives
        # as "True" rather than "on" — the same trap GitHub Actions workflows
        # hit. `events:` is the canonical spelling; `on:` keeps working.
        data = {str(k): v for k, v in item.items()}
        key = next((k for k in ("events", "on", "True") if k in data), None)
        if key is None:
            raise SpecError(
                f"{seat}: missing required key 'events' (the list of events to fire on). "
                f"Known events: {list(EVENTS)}"
            )
        events = _as_str_list(data[key], key, seat)
        if not events:
            raise SpecError(f"{seat}: 'on' must name at least one event. Known: {list(EVENTS)}")
        for ev in events:
            if ev not in EVENTS:
                raise SpecError(
                    f"{seat}: unknown event {ev!r}. Known: {list(EVENTS)}"
                )
        hooks.append(Hook(
            on=tuple(events),
            run=_as_str(_require(data, "run", seat), "run", seat),
            name=_as_str(data["name"], "name", seat) if data.get("name") else "",
        ))
    return hooks


def _parse_phase(raw: object, where: str, blocks: dict[str, Block]) -> Phase:
    if not isinstance(raw, dict):
        raise SpecError(f"{where}: each phase must be a mapping, got {raw!r}")
    data = {str(k): v for k, v in raw.items()}
    pid = _check_id(_as_str(_require(data, "id", where), "id", where), "id", where)
    seat = f"{where} phase '{pid}'"

    block_id = data.get("block")
    block: Block | None = None
    if block_id is not None:
        block_id = _as_str(block_id, "block", seat)
        if block_id not in blocks:
            raise SpecError(
                f"{seat}: unknown block '{block_id}'. Known blocks: {sorted(blocks)}. "
                f"Add one under blocks/ — see docs/03-adding-a-block.md"
            )
        block = blocks[block_id]

    disabled = set(_as_str_list(data.get("disable"), "disable", seat))
    inherited = [c for c in (block.criteria if block else []) if c.id not in disabled]
    own = _parse_criteria(data.get("criteria"), seat)
    for missing in disabled - {c.id for c in (block.criteria if block else [])}:
        raise SpecError(
            f"{seat}: 'disable' names criterion '{missing}', which block "
            f"'{block_id}' does not define"
        )

    artifact = data.get("artifact")
    resolved_artifact = _as_str(artifact, "artifact", seat) if artifact is not None else (
        block.artifact if block else None
    )

    iterate = _as_bool(data.get("iterate"), "iterate", seat, block.iterate if block else False)
    max_iterations = _as_int(
        data.get("max_iterations"), "max_iterations", seat,
        block.max_iterations if block else 0,
    )

    return Phase(
        id=pid,
        name=_as_str(data.get("name") or (block.name if block else pid), "name", seat),
        block=block_id,
        artifact=resolved_artifact,
        depends_on=_as_str_list(data.get("depends_on"), "depends_on", seat),
        criteria=_merge_criteria(inherited, own),
        guidance=str(data.get("guidance") or (block.guidance if block else "")).strip(),
        optional=_as_bool(data.get("optional"), "optional", seat, False),
        iterate=iterate,
        max_iterations=max_iterations,
        archive=_as_bool(data.get("archive"), "archive", seat, block.archive if block else False),
        cost_aware=_as_bool(data.get("cost_aware"), "cost_aware", seat, block.cost_aware if block else False),
        run=_as_str(data["run"], "run", seat) if data.get("run") else None,
    )


def _check_dependencies(phases: list[Phase], where: str) -> None:
    """Dependencies must exist and must point backwards.

    Forward references are rejected rather than topologically sorted on the
    author's behalf: a pipeline file whose reading order differs from its
    execution order is a pipeline nobody can review.
    """
    position = {p.id: i for i, p in enumerate(phases)}
    for p in phases:
        for dep in p.depends_on:
            if dep not in position:
                raise SpecError(
                    f"{where}: phase '{p.id}' depends on '{dep}', which is not a phase in this "
                    f"pipeline. Known: {list(position)}"
                )
            if dep == p.id:
                raise SpecError(f"{where}: phase '{p.id}' depends on itself")
            if position[dep] > position[p.id]:
                raise SpecError(
                    f"{where}: phase '{p.id}' depends on '{dep}', which is declared later in the "
                    f"file. List phases in execution order."
                )

# Writing a pipeline

```bash
agent-pipeline init my-pipeline      # writes pipelines/my-pipeline.yaml
agent-pipeline validate              # costs nothing, catches everything structural
```

Run `validate` before anything executes. It loads and checks the whole
definition without doing work or spending money — a broken pipeline caught here
is free, and the same break caught three paid steps in is not.

## The file

```yaml
name: blog-post                # lowercase, becomes a CLI argument
description: One line on what this produces.
mode: referee                  # referee | runner
workdir: runs/{run}            # {run} comes from --run

phases:
  - id: research
    block: research

  - id: draft
    block: creative
    depends_on: [research]

  - id: signoff
    block: approval
    depends_on: [draft]
```

## Phase keys

| Key | Meaning |
|---|---|
| `id` | required, lowercase, unique |
| `name` | human label; defaults to the block's name |
| `block` | the block to inherit from |
| `artifact` | path template; defaults to the block's |
| `depends_on` | phases that must complete first — **and the staleness graph** |
| `criteria` | extra criteria, or overrides of the block's by id |
| `disable` | block criteria to drop, by id |
| `guidance` | replaces the block's guidance for this phase |
| `optional` | may be skipped without blocking `ship` |
| `run` | the command, **runner mode only** |
| `iterate` / `max_iterations` / `archive` | override the block's iteration policy |
| `cost_aware` | this phase spends money |

## Templates

`artifact` and `run` are rendered with:

| Variable | Value |
|---|---|
| `{run}` | the `--run` id |
| `{workdir}` | absolute working directory |
| `{root}` | project root |
| `{engine}` | where agent-pipeline is installed — use this for built-in checks |
| `{phase}` | the current phase id |
| anything else | supply with `--var key=value` |

`{artifact}` is additionally available inside a criterion's `run`, and resolves
to that phase's artifact path.

## depends_on is load-bearing

It is not documentation. It decides three things:

1. **What may start.** `start` refuses while a dependency is incomplete.
2. **What counts as stale.** An artifact older than something it depends on is
   stale, and `ship` refuses.
3. **What `reopen` destroys.** Only the real dependents are invalidated —
   which is why regenerating one asset does not throw away the voiceover.

Under-declare it and you get silent staleness. Over-declare it and `reopen`
becomes scorched earth. Declare what a phase actually reads.

## Adapting a block to one phase

Two mechanisms, both in this example:

```yaml
  - id: outline
    block: creative
    depends_on: [research]
    disable: [has-turn]          # an outline has no turn yet
    criteria:
      - id: substance            # same id as the block's -> replaces it
        kind: mechanical
        description: Real structure, not three bullets.
        run: python3 {engine}/checks/min_substance.py {artifact} --min-words 15 --min-lines 4
```

Reach for `disable` and an override before you fork a block. A second block that
differs from the first by one threshold is two blocks to maintain and one of
them will drift.

## Optional phases

`optional: true` means `ship` will not block on it. Use it for genuinely
conditional work — a revision pass that only runs when review found something.
Do not use it to make an inconvenient gate go away; that is what `--force` is
for, and `--force` is recorded.

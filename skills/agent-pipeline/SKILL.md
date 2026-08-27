---
name: agent-pipeline
description: Walk, build or repair a gated pipeline with the agent-pipeline engine. Use whenever a repo has a pipeline YAML (pipeline.yaml or pipelines/*.yaml) and work needs to move through phases, whenever asked to build a repeatable process an agent will follow, and whenever a step must not be skippable. Covers the start/guide/complete loop, recording judged and human verdicts with evidence, staleness, surgical reopen, cost logging, writing a pipeline, and adding a block. Triggers on - pipeline, phase, gate, gated, agent-pipeline, blocks, criteria, complete the phase, ship gate, pipeline status, stale, reopen, why was this refused, build a pipeline, make this repeatable, make sure it can't skip steps.
---

# agent-pipeline

A referee for multi-step work. It never does the work and never decides whether
an idea is good. It answers three questions only — may this **start**, may this
**close**, has anything gone **stale** — and when the answer is no, it says why.

Install: `pip install agent-pipeline` · Source: https://github.com/Oxde/Agent-Pipeline

## Am I in one?

```bash
ls pipeline.yaml pipelines/*.yaml 2>/dev/null && agent-pipeline status
```

If a pipeline exists, **walk it.** Do not improvise an order, do not do the work
first and reconcile the ledger afterwards, and do not skip a phase because it
looks unnecessary — the gates encode failures that already happened.

## The loop

```bash
agent-pipeline status              # ALWAYS start here — where the run actually is
agent-pipeline guide <phase>       # what this phase expects and how it will be judged
agent-pipeline start <phase>       # refuses if a dependency is incomplete
#   ... do the work. Write the artifact the guide named. ...
agent-pipeline complete <phase>    # runs every gate
```

`complete` refuses with the exact cause. **The refusal is the specification** —
read it, fix the cause, run it again. Do not work around it.

Some pipelines need a `--var`, most often the run subject:

```bash
agent-pipeline --run 2026-08-27 --var slug=MyThing status
```

## Recording verdicts

Criteria have a **kind**, and the kind decides who may satisfy it.

| Kind | Who | How |
|---|---|---|
| `mechanical` | the engine, itself, every time | you cannot touch it — fix the artifact |
| `judged` | a model answering a specific question | `agent-pipeline judge <phase> <criterion> --status pass\|fail --evidence "..."` |
| `human` | a person | `agent-pipeline approve <phase> <criterion> --status pass\|fail --note "..."` |

Four rules:

1. **Answer the question that was asked.** `guide` prints each criterion's `ask`
   with its FAIL condition. Answer that one, not a friendlier version.
2. **Evidence means evidence.** Quote the line, name the file, give the number.
   A passing verdict with no evidence is refused, and rightly.
3. **FAIL is a normal outcome.** Record it, fix the artifact, judge again.
   Passing something you would not defend leaves a record saying somebody
   checked, which is worse than no record.
4. **Never assert a mechanical criterion.** The engine runs those and will
   refuse your opinion. Fix the artifact instead.
5. **`independence: N` means N distinct judges.** Spawn a fresh subagent per
   verdict and give each a distinct `--by` name — re-recording under one name
   replaces that verdict, it does not add. The refusal shows the tally
   (`1/3 independent verdicts`). Never satisfy a panel by inventing names for
   work one context did: the point is fresh eyes, and the ledger records who
   judged what.

A `human` criterion is not yours to answer. Present what is being approved,
the cost of proceeding, and the question — then stop and wait.

## What gets you refused

| Refusal | Fix |
|---|---|
| `dependency-incomplete` | complete the named phase first |
| `artifact-missing` / `artifact-empty` | write the artifact `guide` named |
| `criterion-failed` (mechanical) | fix the artifact; the check re-runs every time |
| `criterion-unanswered` | `judge` or `approve` it, with evidence |
| `artifact-stale` | an upstream changed after this was written — rebuild it |
| `phase-forced` at ship | a gate never passed; re-run it properly |

**Writing a stub so the file exists does not work.** `no-placeholders` and
`min-substance` run on most artifacts; a heading with TODO under it is not a
completed phase.

## Stale, and reopening

An artifact older than something it depends on is stale — everything green,
built from a version of its input that no longer exists. `status` marks it `⚠`
and `ship` refuses on it.

```bash
agent-pipeline reopen <phase> --reason "what changed and why"
```

Reopen invalidates **only the real dependents**, so unaffected work survives.
`--reason` is required: it is the only record of why a completed phase stopped
counting, and it is the brief for the next attempt.

## Spending money

```bash
agent-pipeline cost <phase> --usd 0.63
```

Log it as you go. Unlogged spend is why nobody can answer what a piece cost.

## Finishing

```bash
agent-pipeline ship             # every required phase complete, nothing stale, nothing forced
agent-pipeline graph --status   # a mermaid diagram of the run, for a PR or a README
agent-pipeline report --open    # one self-contained HTML page of every run — hand this to a human
```

If the pipeline declares `notify:`, the engine messages the user by itself on
`phase_started` / `phase_completed` / `phase_blocked` / `approval_needed` /
`run_shipped`. You do not need to also tell them — and `approval_needed` fires
on its own when a human criterion is what is holding the run up.

Exit codes: `0` allowed · `1` refused · `2` bad request. Usable from hooks and cron.

## Building a pipeline

```bash
agent-pipeline init <name>       # scaffolds pipelines/<name>.yaml
agent-pipeline blocks            # what phase types already exist
agent-pipeline validate          # costs nothing; run it before anything executes
```

```yaml
name: blog-post
mode: referee          # referee = an agent walks it · runner = the engine drives
workdir: runs/{run}

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

Four things to get right:

**Use a block where one fits.** `creative` `research` `review` `approval`
`generate` `publish`. They carry the craft rules so the pipeline carries only
the sequence. Adapt one with `disable:` and a same-id criterion override before
writing a new block.

**`depends_on` is load-bearing**, not documentation. It gates starting, defines
staleness, and scopes reopen. Declare what a phase actually *reads*.
Under-declare and staleness goes silent; over-declare and reopen becomes
scorched earth.

**Every phase needs something verifiable** — an artifact, a blocking criterion,
or both. `validate` warns when a phase has neither, because such a phase
completes on someone's say-so.

**Pick the mode deliberately.** `referee` when one agent must hold the whole
scope for the output to cohere. `runner` when nobody is watching — the engine
holds the loop and the agent cannot skip, because it never chooses what is next.

## Adding a block

Only when a *kind of work* recurs across pipelines. Drop a YAML into
`<project>/blocks/`; no registration.

Give it at least one **mechanical** criterion (the only gates that cannot be
talked past), then one or two **judged** ones whose `ask` demands a quote, a
name, or a number — a criterion answerable with an adjective always passes.
Three to five blocking criteria per phase; more get answered carelessly.

Full guide: `docs/03-adding-a-block.md` · criteria design: `docs/04-criteria.md`

## The thing to internalise

Every gate exists because something shipped that should not have. The engine is
not doubting you — it is doubting *memory*, including its own. When it refuses
it is usually right, and fixing the cause is cheaper than every path that starts
with working around it.

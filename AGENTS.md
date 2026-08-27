# For the agent

This file is addressed to you, not to a human reading over your shoulder. If
you have been pointed at a repository that uses `agent-pipeline`, read this
before you start work.

```bash
pip install git+https://github.com/Oxde/Agent-Pipeline.git
```

If you are working inside a skill-aware runtime, the shorter operational
version is `skills/agent-pipeline/SKILL.md` — same rules, less prose.

## What this engine is to you

It is a referee, not a manager. It will not tell you how to write, what the
piece should say, or whether an idea is good. It decides three things only:

- whether a phase is allowed to **start**
- whether a phase is allowed to **complete**
- whether anything has gone **stale**

Everything else is yours.

## The loop

```bash
agent-pipeline status              # where the run actually is — start here, always
agent-pipeline guide <phase>       # what this phase expects, and how it will be judged
agent-pipeline start <phase>       # opens it; refuses if dependencies aren't done
#   ... do the work. Write the artifact the guide named. ...
agent-pipeline complete <phase>    # runs every gate
```

`complete` will refuse and tell you exactly why. Read the refusal, fix the
cause, run it again. The refusal is the specification.

## Recording verdicts

Some criteria cannot be run by a machine. Those you answer, and the answer is
recorded:

```bash
agent-pipeline judge <phase> <criterion> --status pass \
  --evidence "the specific thing that makes this true"
```

Four rules about this, and they are the whole point of the engine:

**Answer the question that was asked.** Run `guide <phase>` and read the `ask:`
text. It is a specific question with a specific failure condition. Answer that
one.

**Evidence means evidence.** Quote the line. Name the file. Give the number. "It
reads well" is not evidence, and a passing verdict without evidence is refused.

**FAIL is a normal outcome.** A judged criterion you fail is the system doing
its job. Record the failure, fix the artifact, judge again. What is not
acceptable is passing something you would not defend, because the verdict
outlives you and the next person reads it as fact.

**You cannot assert a mechanical criterion.** The engine runs those itself,
every time, and will not accept your opinion about them. Do not try; fix the
artifact instead.

## Things that will get you refused

**Writing a stub so the file exists.** `no_placeholders` and `min_substance`
run on most artifacts. A heading with TODO under it is not a completed phase.

**Skipping ahead.** `start` refuses when a dependency is incomplete. If you
think a phase is unnecessary, mark it `optional: true` in the pipeline and say
why — do not work around it.

**`--force`.** It exists, it requires `--reason`, it is recorded permanently in
the ledger, and `ship` reports every forced phase by name. Use it when a human
told you to, not to get unstuck.

**Leaving stale work.** If you rebuild an upstream artifact, everything
downstream of it is now stale. `status` will show it and `ship` will refuse.
Run `reopen <phase> --reason '...'` — it invalidates only the real dependents,
so nothing unaffected is thrown away.


## Showing your work

```bash
agent-pipeline graph --status     # a mermaid diagram of the run — good in a PR or a report
agent-pipeline report --out status.html
```

`report` writes ONE self-contained HTML file covering every run under the root:
each phase's state, what is blocking anything in progress, verdicts with their
evidence, and cost per phase. No server, no network, no JavaScript.

Generate it when a human asks where things stand, when you finish a session, or
at the end of an unattended run. It is the artifact a person can read without
running anything, which is usually the difference between them seeing the state
of the work and them asking you for it.

## Telling the user

If the pipeline declares `notify:`, **the engine already messages them** on
`phase_started`, `phase_completed`, `phase_blocked`, `approval_needed` and
`run_shipped`. Do not also send your own duplicate message.

`approval_needed` fires by itself the moment a human criterion is what is
holding the run up — so when you hit an approval gate, record nothing, invent
nothing, and wait. They have been told.

Check whether hooks exist before assuming either way:

```bash
grep -A3 '^notify:' pipeline.yaml pipelines/*.yaml 2>/dev/null
```

If there are none and the run needs a person, say so in your own reply instead.

## Costs

If a phase spends money, log it as you go:

```bash
agent-pipeline cost <phase> --usd 0.63
```

Unlogged spend is why nobody can ever answer what a piece cost, and the real
answer is always higher than remembered.

## Adapting the pipeline

If you have been asked to build a new pipeline rather than walk one:

1. Read `docs/02-writing-a-pipeline.md`, then `agent-pipeline blocks`.
2. Use existing blocks wherever one fits. Reach for a new block only when a
   step type genuinely recurs — see `docs/03-adding-a-block.md`.
3. Run `agent-pipeline validate` before anything runs. It costs nothing and
   catches a broken pipeline for free rather than three paid steps in.
4. Keep `agent_pipeline/` domain-agnostic. Anything that knows about the work belongs
   in a pipeline or a block. If you find yourself editing `agent_pipeline/` to make
   your pipeline work, the pipeline is wrong.

## The one thing worth internalising

Every gate here exists because something shipped that should not have. The
engine is not doubting you specifically — it is doubting *memory*, including
its own. When it refuses, it is usually right, and the two minutes spent fixing
the cause are cheaper than every path that starts with working around it.

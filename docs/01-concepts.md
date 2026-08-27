# Concepts

Five nouns. That is the whole model.

## Pipeline

An ordered list of phases in one YAML file. The order in the file is the
execution order — forward references are rejected rather than silently
topologically sorted, because a file whose reading order differs from its
execution order is a file nobody can review.

## Phase

One step. A phase declares what it produces (`artifact`), what it needs
(`depends_on`), and what must be true of its output (`criteria`).

A phase with neither an artifact nor a blocking criterion is not a phase. It
would complete on the say-so of whoever asked, which is the hole this engine
exists to close. `validate` warns about these.

## Block

A reusable phase *type*. The parts of a step that are identical everywhere it
appears: the artifact shape, the criteria, whether it iterates, and the guidance
handed to the worker.

Blocks are why a pipeline file can be almost content-free. `block: creative`
carries the craft rules so the pipeline only has to carry the sequence.

## Criterion

One checkable condition, with a **kind** that decides who may satisfy it —
`mechanical` (the engine runs it), `judged` (a model answers, with evidence) or
`human` (a person decides).

The kind is not a hint. A mechanical criterion cannot be asserted by anyone, and
a judged one cannot pass without evidence. See
[04-criteria.md](04-criteria.md).

## Ledger

The run's state, at `<workdir>/.pipeline/ledger.json`. One entry per phase, and
inside each, one **verdict** per criterion — what was decided, by whom, on what
evidence, when.

The rule that makes it worth anything: **a claim is not a verdict.** Nothing is
written here because someone said a step was done. Mechanical verdicts are
written by the engine after it ran the command. Judged and human verdicts are
written only through an explicit call carrying evidence.

---

## Two modes

The same pipeline runs in either.

**`referee`** — the agent walks it, the engine refuses bad transitions. Correct
when one worker needs the whole scope in its head, and slicing the work into
isolated steps would break the thing that makes it coherent.

**`runner`** — the engine walks it, calling each phase's `run` command. Correct
when nobody is watching. The worker cannot skip a step because it never decides
what comes next.

Most real setups use both: referee for the long creative artifact, runner for
the daily unattended one.

## What "stale" means

A phase is stale when its artifact's mtime is **older than an artifact it
depends on**. Not missing. Not failing. Complete, green, and built on an input
that has since moved.

This is the failure that survives every other check, because everything looks
fine. `status` marks it and `ship` refuses on it.

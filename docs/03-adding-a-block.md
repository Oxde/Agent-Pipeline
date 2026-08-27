# Adding a block

A block is a reusable phase type. You are writing one when a *kind of step*
recurs across pipelines — not when one pipeline needs one different threshold.

## When NOT to write one

- **One phase needs a tweak.** Use `disable:` and a criterion override in the
  phase. See [02-writing-a-pipeline.md](02-writing-a-pipeline.md).
- **It would differ from an existing block by one criterion.** Extend it
  (`extends:`) instead. Two near-identical blocks means two to maintain, and one
  of them drifts.
- **It is really one pipeline's whole shape.** That is a pipeline, not a block.

Write a block when you can name the *type of work* without naming the project:
"a translation pass", "a compliance review", "a data extraction".

## Where they live

```
blocks/                     built-in, ships with the engine
<your project>/blocks/      yours — loaded alongside, and shadows a built-in
                            if you reuse its id
```

No registration. Drop the file in; `agent-pipeline blocks` will list it.

## The anatomy

```yaml
id: translate                    # required, lowercase, unique
name: Translation                # required, shown in status
description: >                   # required — one paragraph, says when to use it
  Rendering an approved source text into another language for a market that
  will read it as native. Not a mechanical conversion: the output is judged on
  whether a native reader would notice it was translated.

extends: creative                # optional — inherit another block's criteria
artifact: "{workdir}/{phase}.md" # optional — default artifact shape
iterate: true                    # optional — may have multiple attempts
max_iterations: 5
archive: true                    # keep every attempt in the ledger
cost_aware: false                # true if this phase spends money

guidance: |                      # handed to whoever does the work
  Translate for the reader, not for the source. A sentence that is faithful and
  lands wrong is a failed translation.

  Do a literal back-translation of every claim before you finish. If the
  back-translation says something the original did not, you have written new
  copy in another language and it has not been reviewed.

criteria:
  - id: back-translated
    kind: mechanical
    description: A back-translation section exists.
    run: python3 {engine}/checks/contains.py {artifact} --pattern "^## Back-translation" --min 1 --multiline

  - id: reads-native
    kind: judged
    description: A native reader would not notice this was translated.
    ask: >
      Name the most translated-sounding sentence in this text and say why. If
      no sentence stands out, answer PASS and quote the one you were least sure
      about.
```

## Writing the guidance

Guidance is not documentation. It is what the worker reads immediately before
starting, so it should say the things that are true *every time this kind of
work happens*, and nothing else.

What belongs: the failure mode this type of work falls into by default. What
"good" looks like specifically enough to act on. The order to attack things in.

What does not: project background, tool instructions, anything a single
pipeline could say better in its own `guidance:` override.

Aim for something a competent worker could act on cold. If it reads like a
policy document, it will be skimmed, and skimmed guidance is guidance that
decayed before the work started.

## Choosing criteria

Three to five. Fewer and the block does not bite; more and they get answered
carelessly, which is worse than not asking.

**Every block should carry at least one mechanical criterion**, even a trivial
one. Mechanical checks are the only gates that cannot be talked past, and
`no_placeholders` alone closes the single most common cheat — a phase completing
because a file exists, where the file is a skeleton.

Then one or two judged criteria that name the failure mode this work actually
has. Not "is it good" — that gets a yes every time. Ask a question whose FAIL
condition is concrete:

| Weak | Bites |
|---|---|
| Is the writing high quality? | Quote the line where the reader's expectation is overturned. If none exists, FAIL. |
| Is it on brand? | Could a direct competitor publish this unchanged? If yes, FAIL and quote the most generic line. |
| Is the research thorough? | Name one thing here that could NOT have been written before the research began. |

The pattern: **demand a quote, a name, or a number.** A criterion that can be
satisfied with an adjective will be.

More on this in [04-criteria.md](04-criteria.md).

## Extending an existing block

```yaml
id: compliance-review
name: Compliance review
extends: review
description: >
  A review pass that additionally checks published claims against the rules we
  are actually bound by.

criteria:
  - id: no-absolute-claims
    kind: mechanical
    description: No "guaranteed", "cures", "proven" in customer-facing text.
    run: python3 {engine}/checks/contains.py {artifact} --pattern "(?i)(guarantee|cures|clinically proven)" --max 0
```

Child criteria with the same `id` replace the parent's; the rest append. The
parent's guidance is inherited unless you write your own.

## Test it before you trust it

```bash
agent-pipeline blocks                      # is it loaded, and does the summary read right?
agent-pipeline validate                    # does a pipeline using it resolve?
agent-pipeline guide <phase>               # is this what a worker should see?
```

Then run the block's mechanical checks by hand against a known-bad artifact and
confirm they **fail**. A check that has never failed is a check nobody has
tested, and a check that cannot fail is decoration.

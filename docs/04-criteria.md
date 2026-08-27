# Criteria that actually bite

A criterion is one checkable condition, and its `kind` decides who may satisfy
it. That is not a formality — it is the whole reason the engine is worth
running.

| Kind | Satisfied by | Can it be asserted? |
|---|---|---|
| `mechanical` | a command the engine runs itself, every time a phase closes | never |
| `judged` | a model answering a specific question | only with evidence |
| `human` | a person deciding | only by a person |

## mechanical

```yaml
- id: sources
  kind: mechanical
  description: At least three sources are cited.
  run: python3 {engine}/checks/contains.py {artifact} --pattern https?:// --min 3
```

Exit 0 passes, anything else fails, and stdout/stderr becomes the evidence.

The engine re-runs these on **every** completion attempt rather than trusting a
previous verdict. A mechanical result carried over from an earlier attempt is
exactly how a check that used to pass keeps passing after the artifact changed
underneath it.

Three generic checks ship with the engine:

| Check | Use |
|---|---|
| `checks/no_placeholders.py` | no TODO / TBD / lorem left |
| `checks/min_substance.py` | enough body to be the work it claims to be |
| `checks/contains.py` | any regex, with `--min` / `--max` |

`contains.py` covers most real gates without writing Python. Reach for a custom
script when the rule needs to look at structure rather than text.

## judged

```yaml
- id: has-turn
  kind: judged
  description: Something changes — an expectation is set, then overturned.
  ask: >
    Quote the line where the reader's expectation is overturned. If the piece
    only accumulates and never turns, answer FAIL.
```

The engine cannot run this, so it demands a record:

```bash
agent-pipeline judge draft has-turn --status pass \
  --evidence "'But that is not what the record shows' overturns the opening."
```

An unanswered blocking judged criterion refuses the phase. A pass with no
evidence is refused. Both refusals exist because "I checked" is not a check.

### Writing an `ask` that bites

The failure mode of judged criteria is that everything passes. That happens when
the question can be answered with an adjective.

**Demand a quote, a name, or a number.** Force the answer to point at something
in the artifact:

> Quote the line where… · Name one thing that… · State in one sentence…

**Write the FAIL condition into the question.** Not "is this specific enough"
but "could a competitor publish this unchanged? If yes, FAIL and quote the most
generic line."

**Ask about one thing.** A criterion checking coherence *and* tone gets a split
answer and a confident PASS.

**Prefer questions whose honest answer is sometimes no.** If you cannot imagine
this criterion failing, it is not a gate, it is a comment.

## human

```yaml
- id: approved
  kind: human
  description: A person has seen this and decided to proceed.
  ask: Approve proceeding past this gate?
```

```bash
agent-pipeline approve signoff approved --status pass --note "ship it"
agent-pipeline approve signoff approved --status fail --note "the close still doesn't answer the open"
```

A `fail` with a note is the most useful thing a human gate produces: the note is
the instruction for the next attempt. Record it, then `reopen` the phase it
applies to.

Put a human criterion immediately before anything expensive or irreversible.
That is where a two-minute stop saves the most.

## blocking: false

```yaml
- id: reading-level
  kind: judged
  description: Roughly reads at eighth grade.
  blocking: false
```

Advisory criteria are recorded and shown in `status -v` but never refuse a
phase. Use them for things worth tracking that should not stop the line — and
sparingly, because a wall of advisory criteria is a wall nobody reads.

## How many

Three to five blocking criteria per phase.

Below three and the phase is barely gated. Above five and they get answered
carelessly — and a carelessly-passed criterion is worse than an absent one,
because it leaves a record saying somebody checked.

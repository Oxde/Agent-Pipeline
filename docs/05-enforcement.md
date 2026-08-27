# Making gates unskippable

The engine refuses transitions. But an agent that never calls the engine has not
been refused anything — it has simply not asked. This is the last hole, and it
is the one that actually matters.

Four mechanisms, weakest to strongest.

## 1. Declare the artifact (weak, free)

A phase with a declared artifact cannot complete without a non-empty file there.
Stops nothing deliberate, catches plenty of drift, costs one line.

## 2. Mechanical criteria (medium)

The engine runs these itself. `no_placeholders` and `min_substance` together
close the stub cheat: a file that exists but is a skeleton.

Weakness: still requires someone to call `complete`.

## 3. Hard data dependency (strong — prefer this)

**Make the next step physically unable to run.**

Not a rule. Physics. If the assembly step takes the previous step's artifact as
a command-line argument, a missing upstream artifact is a crash, not an
oversight:

```yaml
  - id: assemble
    depends_on: [beats]
    run: ./assemble --beats {workdir}/beats.json --out {artifact}
```

No enforcement layer is involved. Nothing has to remember anything. Skipping
becomes impossible rather than forbidden, which is a different category of
guarantee, and it costs nothing to arrange.

Design pipelines so each step *consumes* the previous step's output rather than
merely following it. Where you can arrange that, you barely need the rest of
this page.

## 4. External enforcement (strongest for referee mode)

Put the check outside the worker's control, so calling it is not a decision.

**Claude Code** fires hooks on lifecycle events, running shell scripts the model
has no vote on. A `Stop` hook that runs the ship gate turns "the agent
remembered to verify" into "the session cannot end unverified":

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "agent-pipeline --root . status && agent-pipeline --root . ship"
          }
        ]
      }
    ]
  }
}
```

**Cron** should invoke the runner, never the agent:

```cron
# wrong — the agent holds the loop and can skip
0 9 * * *  claude -p "run the daily post pipeline"

# right — the engine holds the loop; the agent is a subroutine
0 9 * * *  agent-pipeline --pipeline pipelines/daily-post.yaml --run $(date +\%F) run
```

That single change is the highest-value line on this page for unattended work.
An agent invoked per phase by the runner cannot skip a phase, because it never
decides what comes next.

**Git hooks / CI** work the same way — `agent-pipeline ship` exits non-zero, so
any shell can gate on it.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | allowed / clean |
| `1` | refused — a gate said no |
| `2` | the request was wrong — unknown phase, bad spec, missing file |

`1` and `2` mean different things and should be handled differently: `1` is the
system working, `2` is something misconfigured.

## About `--force`

It exists because reality does. It requires `--reason`, records the reason
permanently, and `ship` reports every forced phase by name:

```
✗ [phase-forced] 'review' was force-completed, so its gates never passed.
```

`ship --allow-forced` will proceed, and that is a deliberate act with a name on
it. What `--force` must never become is the normal way past a gate that is
inconvenient — at which point the gate is wrong and should be changed in the
pipeline, where the change is visible in a diff.

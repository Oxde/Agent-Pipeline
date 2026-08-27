# Notifications

An agent that talks to a person — over Telegram, Slack, email, a webhook — needs
to say two things: *this step is done*, and, far more importantly, *I need you
before I can continue*.

A pipeline declares commands; the engine runs them on lifecycle events.

```yaml
name: daily-post
mode: runner

notify:
  - name: telegram
    events: [approval_needed, phase_blocked, run_shipped]
    run: ./notify/telegram.sh
```

```bash
#!/bin/sh
# notify/telegram.sh — everything arrives as environment variables
curl -sS -X POST "https://api.telegram.org/bot$TG_TOKEN/sendMessage" \
  --data-urlencode "chat_id=$TG_CHAT" \
  --data-urlencode "text=$AGENT_PIPELINE_MESSAGE" > /dev/null
```

That is the whole integration. What the user receives:

```
[daily-post · 2026-08-27] ✓ finished draft
Draft

[daily-post · 2026-08-27] ✋ needs you: signoff
• [approved] A person has seen this and decided to proceed. — no verdict recorded.
```

## Events

| Event | Fires when |
|---|---|
| `phase_started` | a phase opens |
| `phase_completed` | a phase closes, all gates passed |
| `phase_blocked` | `complete` was refused |
| `approval_needed` | refused **specifically** because a human criterion has no verdict |
| `run_shipped` | `ship` passed |

`approval_needed` is the one worth wiring first. It is the difference between an
agent that waits silently and one that tells you it is waiting.

Note it is a *narrowing* of `phase_blocked` — a run stuck on a failing check
fires `phase_blocked`, while one stuck on you fires `approval_needed`. Subscribe
to both if you want everything; to just `approval_needed` if you only want to be
interrupted when you are actually the blocker.

## What the command receives

Everything arrives as environment variables:

| Variable | Contents |
|---|---|
| `AGENT_PIPELINE_MESSAGE` | a formatted, human-readable line — usually all you need |
| `AGENT_PIPELINE_EVENT` | the event name |
| `AGENT_PIPELINE_PIPELINE` | pipeline name |
| `AGENT_PIPELINE_RUN` | run id |
| `AGENT_PIPELINE_PHASE` | phase id, where one applies |
| `AGENT_PIPELINE_DETAIL` | the blockers, or the phase name |

The command line itself may only template `{event}`, `{phase}`, `{run}` and
`{pipeline}`.

**This restriction is deliberate.** Blocker text contains criterion descriptions
and evidence — arbitrary content — and splicing arbitrary content into a shell
command is how a review note containing a quote becomes a shell injection.
Identifiers can reach the command line; free text cannot, and a hook that tries
to template `{message}` fails loudly instead.

## Two guarantees

**A notification can never break a run.** Every hook is fire-and-forget: a dead
webhook, a bad token, a missing script or a timeout is reported on stderr and
ignored. Work does not stop because a message failed to send.

**`--no-notify` suppresses everything.** Useful when replaying a run, testing a
pipeline, or backfilling a ledger — nobody wants six hours of history delivered
to their phone at once.

## Where this fits with runner mode

Runner mode plus notifications is the unattended setup:

```cron
0 9 * * *  agent-pipeline --pipeline pipelines/daily-post.yaml --run $(date +\%F) run
```

The engine walks the pipeline, the agent does each step, and the first time it
genuinely needs a person it stops and tells you why. You answer in chat; your
agent records the verdict and runs again — the completed phases are served from
the ledger, so only the part after your correction re-executes.

## Seeing everything at once

```bash
agent-pipeline report --open
```

One self-contained HTML file: every run under the root, each phase with its
state, what is blocking it, verdicts with their evidence, and cost per phase.
No server, no build, no network — a file you open by double-clicking.

`--out <path>` to choose the location, `--title` for the heading. Regenerate it
from a cron job and you have a status page; hand it to someone and they can read
it with no tooling at all.

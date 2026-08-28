# Agent-Pipeline stress-test findings

Tested from `/Users/nikmarf/ap-test` against the current `Oxde/Agent-Pipeline` main branch on 2026-08-28.

## Executive verdict

The engine is trustworthy for dependency order, artifact existence, mechanical gates on active phases, staleness, runner caching, and inspectable reports. **Do not yet trust persisted-ledger configuration drift or multi-reviewer panels loaded from externally edited/corrupt JSON.** The original one-verdict `independence: N` bypass is fixed for normally recorded panels, but final adversarial review found three deeper persisted-state bypasses listed below.

This follow-up fixes the three remaining gaps found by the overnight run and final review:

1. replaced same-author verdicts now remain in phase history for audit;
2. `ship` now reports staleness even when the stale phase is still active, instead of only saying the phase is incomplete;
3. a verdict recorded for an earlier criterion kind can no longer satisfy, fail, or count toward a criterion that later reuses the ID with a different kind.

That third fix is complete for active-phase completion, CLI tallies, status, and HTML evidence. It is **not sufficient for already-completed phases**, because `ship` and runner caching do not revalidate a completed phase after its pipeline criteria change.

## Open security blockers for the main developer agent

1. **Completed-phase configuration drift:** `check_can_ship` trusts `status: complete` and does not revalidate current criteria. A phase completed with a judged PASS can remain shippable after the criterion changes to human or a new criterion is added. Runner mode also skips completed phases. Recommended direction: persist and compare a normalized phase/spec fingerprint, invalidating or revalidating completed phases when it changes.
2. **Outer/embedded criterion ID mismatch:** v1/v2 loading accepts a verdict stored under `verdicts["approval"]` even when `Verdict.criterion` is a different ID. Current panel lookup trusts the outer key. Loading should fail closed or current-panel validation must require both IDs to match.
3. **Duplicate authors in loaded v2 panels:** JSON loading does not enforce the one-verdict-per-author invariant. Two persisted PASS entries with the same `by` can satisfy `independence: 2`. Loading/absorption must reject or deterministically normalize duplicates while preserving discarded entries for audit.

These also make current completion counters misleading for configuration-drift cases: status and HTML can say a completed phase/run is done while the current criterion has no valid verdict.

## Findings and disposition

### 1. Multi-reviewer independence bypass

**Original finding:** `review` appeared to complete with `independence: 2` after only one verdict.

**Current result:** **fixed before this follow-up / not reproducible on current main.**

Current behavior:

```text
✗ [criterion-unanswered] [adversarial] ... — 1/2 independent verdicts (so far: one-reviewer).
→ judge review adversarial --status pass --by <distinct-name> --evidence '...'
```

Relevant existing commit: `2942636 independence: N — panels of judges, enforced by refusal`.

Coverage already on main:

- one author repeated three times still counts as `1/3`;
- three distinct authors satisfy `3/3`;
- any recorded FAIL blocks regardless of pass count;
- `validate` renders judged panel criteria as `judged ×N`;
- mechanical criteria reject `independence` because rerunning one engine is not independent review.

### 2. Duplicate verdict under the same `--by`

**Original finding:** a second verdict from the same author replaced the first, making the prior answer disappear from the current panel.

**Security result:** **not an independence bypass on current main.** The panel keeps one current verdict per distinct author, so repeated use of the same `--by` never increases the independence tally.

**Audit gap:** confirmed. The replaced verdict was lost from the ledger's inspectable history.

**Fix in this follow-up:** when the same author re-judges a criterion, the current panel still contains one verdict, but the replaced and replacement verdicts are appended to phase history as a `verdict-replaced` event. Runner mode now also merges subprocess-written history without duplication before saving its parent ledger, including equal-second timestamp collisions.

This preserves both requirements:

- a reviewer can change their mind;
- the audit record still shows what changed.

Regression test: `TestLedgerRoundTrip.test_rejudging_preserves_the_replaced_verdict_in_history`.

### 3. Confusing staleness refusal at ship time

**Original output:**

```text
✗ [phase-incomplete] required phase 'b' (Creative work) is active.
    → Run: start b … complete b
```

This was technically correct but hid the useful cause when `b`'s artifact was also older than its upstream dependency. It also told the user to start a phase that was already active.

**Fix in this follow-up:**

- active phases now receive an active-specific completion hint;
- if their existing artifact is stale, `ship` reports both `phase-incomplete` and `phase-stale`;
- the stale refusal names the newer upstream phase and explicitly says to rebuild from current upstream input.

Regression test: `TestStaleness.test_active_stale_phase_reports_both_reasons_it_cannot_ship`.

### 4. Criterion-kind transition bypass

**Final-review finding:** gates looked up panel entries by criterion ID alone. If a pipeline changed a criterion from `judged` to `human` while retaining its ID, a persisted judged PASS could satisfy the new human approval gate. The same ID-only lookup could also let old human or mechanical verdicts satisfy, fail, or inflate the independence tally of a new kind.

**Fix in this follow-up:** current panels are filtered by both criterion ID and exact kind. A verdict whose `Verdict.kind` differs from the current `Criterion.kind` is inactive evidence: it cannot pass the gate, fail the gate, appear in the current status tally, or increase the CLI independence tally.

Auditability is preserved. Mismatched verdicts remain in the raw ledger panel and survive save/load; when a same-author current-kind verdict replaces one, the existing `verdict-replaced` history event archives both versions. The HTML report labels retained mismatches as inactive rather than displaying them as current approval.

End-to-end persisted-ledger regressions cover:

- `judged` → `human`, using default authors `judge` and `human`, including the later approval and another save/load;
- `human` → `judged`, including an `independence: 2` tally and the default `judge` CLI path;
- `mechanical` → `judged`, loaded from a schema-v1 single-verdict ledger with default author `engine`;
- `mechanical` → `human`, proving an old mechanical FAIL becomes unanswered rather than failing the human gate;
- HTML report rendering of mismatched evidence as inactive rather than approved.

### 5. Stub artifact refusal

**Original finding:** a stub artifact produced several failures at once: placeholder, minimum substance, and unanswered judged criteria.

**Disposition:** **working as designed; no engine fix needed.** The engine correctly refused a file that existed but was not substantive work. Returning all blockers in one attempt is preferable to revealing them one at a time.

The useful behavior is retained:

- TODO/TBD/lorem placeholders fail;
- thin artifacts fail;
- judged gates remain unanswered until evidence is recorded.

### 6. `validate` did not surface `independence: 2`

**Original finding:** `independence` appeared silently accepted or ignored by `validate`.

**Current result:** **fixed before this follow-up.** Current output includes:

```text
· adversarial (judged ×2)
```

The guide also says the criterion requires two independent verdicts.

Relevant existing commit: `2942636 independence: N — panels of judges, enforced by refusal`.

### 7. Runner behavior

**Result:** passed.

Runner mode failed closed when the middle phase failed. After correcting the middle command, the rerun served the first phase from the ledger as cached and continued from the failed point.

No code change needed.

### 8. Reports

**Result:** passed after the kind-transition fix.

The self-contained HTML report includes phase state, blockers, verdict evidence, and cost. It separates current verdicts from inactive evidence whose kind no longer matches the criterion. No server or JavaScript is required.

Regression test: `TestReport.test_mismatched_verdict_is_shown_as_inactive_not_current_approval`.

### 9. Notification callbacks

**Result:** implemented and tested, but not configured in the `/Users/nikmarf/ap-test` pipeline YAML files.

Available events:

- `phase_started`
- `phase_completed`
- `phase_blocked`
- `approval_needed`
- `run_shipped`

Payload is exposed through `AGENT_PIPELINE_*` environment variables. Notification failures are intentionally non-fatal so a dead webhook cannot corrupt pipeline state.

No engine bug was found here. A pipeline must declare `notify:` hooks before callbacks fire.

## Verification

Targeted RED/GREEN tests were added for the three remaining gaps, including each kind transition, then the full suite was run:

```text
Ran 67 tests
OK
```

## Final trust decision

I would use the current engine for unattended work only while the pipeline specification and ledger remain engine-controlled and unchanged during a run. I would **not** trust shipping after pipeline criteria change, or independence claims from loaded/external ledger JSON, until the three blockers above are fixed. Even after those fixes, distinct `--by` labels prove recorded identities, not cryptographic identity or genuinely fresh model context; the orchestrator must still spawn genuinely independent reviewers rather than inventing names.

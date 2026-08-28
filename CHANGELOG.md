# Changelog

Pre-1.0: minor versions add capability, patches fix. 1.0 lands when the last
known integrity gap (completed-phase configuration drift) is closed and the
engine has gated real production work for a while.

Install is always `pip install git+https://github.com/Oxde/Agent-Pipeline.git`.
The bare PyPI name `agent-pipeline` belongs to an unrelated project — never
install it.

## [0.2.0] — 2026-08-28

### Added
- **Independence panels** — `independence: N` on judged/human criteria demands
  N passing verdicts from N distinct authors; one voice re-recording replaces
  itself. Ledger schema 2 (panels), with silent v1 migration.
- **Required reading** — `context:` on phases/blocks; `guide` lists it,
  `start` refuses while a listed file is missing.
- **Notifications** — `notify:` hooks fired on phase/run lifecycle events
  (`approval_needed` narrows `phase_blocked` to "a human is the blocker").
  Fire-and-forget; payload via environment, identifiers-only on command lines.
- **HTML report** — `report`: one self-contained page for every run; no
  server, no JavaScript. **Graph** — `graph [--status]`: mermaid/dot from the
  same YAML the engine executes.
- **Packaging** — installable package with blocks/checks bundled;
  `agent-pipeline` console script; agent skill in `skills/`; `--version`.
- Ledgers record their run `--var`s and the engine version that wrote them.

### Fixed
- Runner absorbs ledger writes made by the phase's own subprocess (judged
  verdicts recorded by an agent no longer vanish on the next save).
- Criterion-kind transitions: a persisted verdict for an earlier kind can no
  longer satisfy, fail, or inflate a criterion that reuses the id with a new
  kind (overnight stress-test finding).
- Verdict replacements are archived to phase history; `ship` names staleness
  on active phases instead of only "incomplete".
- Loader hardening: criterion-id mismatches in persisted JSON fail closed;
  duplicate authors are normalized newest-per-author with discards archived —
  a forged duplicate cannot inflate an independence tally.

### Known open
- Completed-phase configuration drift: `ship` trusts `status: complete`
  without revalidating against current criteria
  (docs/stress-test-findings.md, blocker 1).

## [0.1.0] — 2026-08-27

Initial engine: pipelines as YAML walked in referee or runner mode; phases
with artifacts, dependencies and three criterion kinds (mechanical — engine-
run, judged — evidence required, human); staleness via mtime against declared
dependencies; surgical reopen with archived iterations; six built-in blocks;
three generic checks; plain-text status; 25 generic tests.

"""Engine tests — generic, no domain knowledge.

These assert the guarantees the engine actually sells: that a malformed
pipeline is refused before anything runs, that a phase cannot close on someone's
say-so, that staleness is detected, and that reopening is surgical.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
PACKAGE_ROOT = REPO_ROOT / "agent_pipeline"

from agent_pipeline import (  # noqa: E402
    Context,
    Ledger,
    Verdict,
    check_can_complete,
    check_can_ship,
    check_can_start,
    load_blocks,
    load_pipeline,
    now_iso,
)
from agent_pipeline.spec import SpecError  # noqa: E402


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


class Harness(unittest.TestCase):
    """A throwaway project with a pipeline, a ledger and real files on disk."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.blocks_dir = self.root / "blocks"
        self.blocks_dir.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def blocks(self):
        return load_blocks(PACKAGE_ROOT / "blocks", self.blocks_dir)

    def pipeline(self, yaml_text: str):
        path = write(self.root / "pipelines" / "p.yaml", yaml_text)
        return load_pipeline(path, self.blocks())

    def context(self, run: str = "t1") -> Context:
        workdir = self.root / "runs" / run
        return Context(root=self.root, run=run, workdir=workdir, engine=PACKAGE_ROOT)


# ---------------------------------------------------------------------------


class TestSpecRefusals(Harness):
    """A malformed pipeline must fail at load, before anything is spent."""

    def test_forward_dependency_is_rejected(self) -> None:
        with self.assertRaises(SpecError) as cm:
            self.pipeline("""
                name: p
                phases:
                  - id: a
                    artifact: "{workdir}/a.md"
                    depends_on: [b]
                  - id: b
                    artifact: "{workdir}/b.md"
            """)
        self.assertIn("declared later in the file", str(cm.exception))

    def test_unknown_dependency_names_the_known_phases(self) -> None:
        with self.assertRaises(SpecError) as cm:
            self.pipeline("""
                name: p
                phases:
                  - id: a
                    artifact: "{workdir}/a.md"
                    depends_on: [nope]
            """)
        self.assertIn("'nope'", str(cm.exception))
        self.assertIn("Known", str(cm.exception))

    def test_duplicate_phase_id_is_rejected(self) -> None:
        with self.assertRaises(SpecError):
            self.pipeline("""
                name: p
                phases:
                  - id: a
                    artifact: "{workdir}/a.md"
                  - id: a
                    artifact: "{workdir}/a2.md"
            """)

    def test_unknown_block_lists_what_exists(self) -> None:
        with self.assertRaises(SpecError) as cm:
            self.pipeline("""
                name: p
                phases:
                  - id: a
                    block: nonexistent
            """)
        self.assertIn("unknown block", str(cm.exception))
        self.assertIn("creative", str(cm.exception))

    def test_mechanical_criterion_without_run_is_rejected(self) -> None:
        write(self.blocks_dir / "bad.yaml", """
            id: bad
            name: Bad
            description: A mechanical criterion with nothing to run.
            criteria:
              - id: x
                kind: mechanical
                description: Nothing to run.
        """)
        with self.assertRaises(SpecError) as cm:
            self.blocks()
        self.assertIn("requires 'run'", str(cm.exception))

    def test_judged_criterion_cannot_carry_a_command(self) -> None:
        write(self.blocks_dir / "bad2.yaml", """
            id: bad2
            name: Bad2
            description: A judged criterion pretending to be mechanical.
            criteria:
              - id: x
                kind: judged
                description: Sneaky.
                ask: Is it?
                run: echo yes
        """)
        with self.assertRaises(SpecError) as cm:
            self.blocks()
        self.assertIn("only 'mechanical' runs commands", str(cm.exception))

    def test_block_inheritance_cycle_is_caught(self) -> None:
        write(self.blocks_dir / "x.yaml", """
            id: x
            name: X
            description: Cycles into y.
            extends: y
        """)
        write(self.blocks_dir / "y.yaml", """
            id: y
            name: Y
            description: Cycles into x.
            extends: x
        """)
        with self.assertRaises(SpecError) as cm:
            self.blocks()
        self.assertIn("cycle", str(cm.exception))


class TestBlockResolution(Harness):
    def test_block_criteria_are_inherited(self) -> None:
        p = self.pipeline("""
            name: p
            phases:
              - id: draft
                block: creative
        """)
        ids = {c.id for c in p.phase("draft").criteria}
        self.assertIn("has-turn", ids)
        self.assertIn("no-placeholders", ids)

    def test_disable_drops_one_inherited_criterion(self) -> None:
        p = self.pipeline("""
            name: p
            phases:
              - id: draft
                block: creative
                disable: [has-turn]
        """)
        ids = {c.id for c in p.phase("draft").criteria}
        self.assertNotIn("has-turn", ids)
        self.assertIn("one-point", ids)

    def test_disabling_a_criterion_the_block_lacks_is_an_error(self) -> None:
        with self.assertRaises(SpecError) as cm:
            self.pipeline("""
                name: p
                phases:
                  - id: draft
                    block: creative
                    disable: [not-a-criterion]
            """)
        self.assertIn("not-a-criterion", str(cm.exception))

    def test_same_id_overrides_rather_than_duplicating(self) -> None:
        p = self.pipeline("""
            name: p
            phases:
              - id: draft
                block: creative
                criteria:
                  - id: substance
                    kind: mechanical
                    description: Custom threshold.
                    run: echo ok
        """)
        matches = [c for c in p.phase("draft").criteria if c.id == "substance"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].run, "echo ok")

    def test_extends_merges_parent_criteria(self) -> None:
        write(self.blocks_dir / "child.yaml", """
            id: child
            name: Child
            description: Extends review with one more rule.
            extends: review
            criteria:
              - id: extra
                kind: judged
                description: One more.
                ask: Well?
        """)
        p = self.pipeline("""
            name: p
            phases:
              - id: r
                block: child
        """)
        ids = {c.id for c in p.phase("r").criteria}
        self.assertIn("adversarial", ids)   # from review
        self.assertIn("extra", ids)         # from child


class TestGates(Harness):
    PIPE = """
        name: p
        workdir: runs/{run}
        phases:
          - id: a
            artifact: "{workdir}/a.md"
            criteria:
              - id: mech
                kind: mechanical
                description: File is non-trivial.
                run: python3 {engine}/checks/min_substance.py {artifact} --min-words 5
              - id: think
                kind: judged
                description: Someone thought about it.
                ask: Did they?
          - id: b
            artifact: "{workdir}/b.md"
            depends_on: [a]
    """

    def setUp(self) -> None:
        super().setUp()
        self.p = self.pipeline(self.PIPE)
        self.ctx = self.context()
        self.ledger = Ledger(pipeline="p", run="t1", created_at=now_iso())

    def start(self, phase_id: str) -> None:
        e = self.ledger.entry(phase_id)
        e.status = "active"
        e.started_at = now_iso()

    def test_cannot_start_before_dependency(self) -> None:
        r = check_can_start(self.p, self.ledger, self.p.phase("b"))
        self.assertFalse(r.ok)
        self.assertEqual(r.failures[0].code, "dependency-incomplete")

    def test_unstarted_phase_cannot_complete(self) -> None:
        r = check_can_complete(self.p, self.ledger, self.p.phase("a"), self.ctx)
        self.assertFalse(r.ok)
        self.assertEqual(r.failures[0].code, "not-started")

    def test_missing_artifact_blocks(self) -> None:
        self.start("a")
        r = check_can_complete(self.p, self.ledger, self.p.phase("a"), self.ctx)
        self.assertFalse(r.ok)
        self.assertEqual(r.failures[0].code, "artifact-missing")

    def test_empty_artifact_blocks(self) -> None:
        self.start("a")
        write(self.ctx.workdir / "a.md", "")
        r = check_can_complete(self.p, self.ledger, self.p.phase("a"), self.ctx)
        self.assertFalse(r.ok)
        self.assertEqual(r.failures[0].code, "artifact-empty")

    def test_engine_runs_mechanical_check_itself(self) -> None:
        self.start("a")
        write(self.ctx.workdir / "a.md", "too short\n")
        r = check_can_complete(self.p, self.ledger, self.p.phase("a"), self.ctx)
        self.assertFalse(r.ok)
        self.assertIn("criterion-failed", [f.code for f in r.failures])
        # and it recorded its own verdict, attributed to the engine
        self.assertEqual(self.ledger.entry("a").verdict("mech").by, "engine")

    def test_recorded_mechanical_pass_is_not_trusted_after_the_file_changes(self) -> None:
        self.start("a")
        good = "one two three four five six seven eight\n"
        write(self.ctx.workdir / "a.md", good)
        check_can_complete(self.p, self.ledger, self.p.phase("a"), self.ctx)
        self.assertTrue(self.ledger.entry("a").verdict("mech").passed())

        write(self.ctx.workdir / "a.md", "short\n")
        r = check_can_complete(self.p, self.ledger, self.p.phase("a"), self.ctx)
        self.assertFalse(r.ok)
        self.assertFalse(self.ledger.entry("a").verdict("mech").passed())

    def test_unanswered_judged_criterion_blocks(self) -> None:
        self.start("a")
        write(self.ctx.workdir / "a.md", "one two three four five six seven\n")
        r = check_can_complete(self.p, self.ledger, self.p.phase("a"), self.ctx)
        self.assertFalse(r.ok)
        self.assertIn("criterion-unanswered", [f.code for f in r.failures])

    def test_recorded_judged_verdict_unblocks(self) -> None:
        self.start("a")
        write(self.ctx.workdir / "a.md", "one two three four five six seven\n")
        self.ledger.record_verdict("a", Verdict(
            criterion="think", kind="judged", status="pass",
            evidence="line 1 says so", recorded_at=now_iso(), by="judge",
        ))
        r = check_can_complete(self.p, self.ledger, self.p.phase("a"), self.ctx)
        self.assertTrue(r.ok, [f.message for f in r.failures])

    def test_failing_judged_verdict_blocks(self) -> None:
        self.start("a")
        write(self.ctx.workdir / "a.md", "one two three four five six seven\n")
        self.ledger.record_verdict("a", Verdict(
            criterion="think", kind="judged", status="fail",
            evidence="nobody did", recorded_at=now_iso(), by="judge",
        ))
        r = check_can_complete(self.p, self.ledger, self.p.phase("a"), self.ctx)
        self.assertFalse(r.ok)


class TestStaleness(Harness):
    PIPE = """
        name: p
        workdir: runs/{run}
        phases:
          - id: up
            artifact: "{workdir}/up.md"
          - id: down
            artifact: "{workdir}/down.md"
            depends_on: [up]
    """

    def test_downstream_older_than_upstream_is_stale(self) -> None:
        p = self.pipeline(self.PIPE)
        ctx = self.context()
        ledger = Ledger(pipeline="p", run="t1", created_at=now_iso())

        write(ctx.workdir / "up.md", "first\n")
        time.sleep(0.02)
        write(ctx.workdir / "down.md", "second\n")
        for pid in ("up", "down"):
            e = ledger.entry(pid)
            e.status = "complete"
            e.completed_at = now_iso()

        self.assertTrue(check_can_ship(p, ledger, ctx).ok)

        # touch the upstream — nothing is missing, nothing failed, but the
        # downstream artifact was built from a version that no longer exists.
        os.utime(ctx.workdir / "up.md", None)
        report = check_can_ship(p, ledger, ctx)
        self.assertFalse(report.ok)
        self.assertEqual(report.failures[0].code, "phase-stale")

    def test_forced_phase_is_reported_at_ship(self) -> None:
        p = self.pipeline(self.PIPE)
        ctx = self.context()
        ledger = Ledger(pipeline="p", run="t1", created_at=now_iso())
        for pid in ("up", "down"):
            write(ctx.workdir / f"{pid}.md", "x\n")
            e = ledger.entry(pid)
            e.status = "complete"
        ledger.entry("down").forced = True
        report = check_can_ship(p, ledger, ctx)
        self.assertIn("phase-forced", [f.code for f in report.failures])


class TestReopenScope(Harness):
    def test_dependents_are_transitive_but_not_greedy(self) -> None:
        p = self.pipeline("""
            name: p
            phases:
              - id: research
                artifact: "{workdir}/r.md"
              - id: draft
                artifact: "{workdir}/d.md"
                depends_on: [research]
              - id: review
                artifact: "{workdir}/rev.md"
                depends_on: [draft]
              - id: signoff
                artifact: "{workdir}/s.md"
                depends_on: [draft, review]
              - id: sidecar
                artifact: "{workdir}/side.md"
                depends_on: [research]
        """)
        # reopening review must not touch research, draft, or the sidecar branch
        self.assertEqual(p.dependents_of("review"), ["signoff"])
        # reopening research reaches everything downstream of it
        self.assertEqual(
            sorted(p.dependents_of("research")),
            ["draft", "review", "sidecar", "signoff"],
        )


class TestLedgerRoundTrip(Harness):
    def test_verdicts_and_history_survive_a_save_load(self) -> None:
        workdir = self.root / "runs" / "t1"
        ledger = Ledger(pipeline="p", run="t1", created_at=now_iso())
        ledger.entry("a").status = "complete"
        ledger.entry("a").cost_usd = 1.25
        ledger.record_verdict("a", Verdict(
            criterion="x", kind="judged", status="pass",
            evidence="because", recorded_at=now_iso(), by="judge",
        ))
        ledger.snapshot("a", reason="second pass")
        ledger.save(workdir)

        again = Ledger.load(workdir, "p", "t1")
        self.assertEqual(again.entry("a").status, "complete")
        self.assertEqual(again.entry("a").cost_usd, 1.25)
        self.assertEqual(again.entry("a").verdict("x").evidence, "because")
        self.assertEqual(len(again.entry("a").history), 1)
        self.assertEqual(again.total_cost(), 1.25)


if __name__ == "__main__":
    unittest.main()

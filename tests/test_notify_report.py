"""Tests for outbound events and the HTML report.

The guarantees under test: a notification can never break a run, only safe
identifier fields ever reach a command line, `on:` survives YAML's boolean
trap, and the report is genuinely self-contained.
"""

from __future__ import annotations

import re
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
PACKAGE_ROOT = REPO_ROOT / "agent_pipeline"

from agent_pipeline import Context, Ledger, load_blocks, load_pipeline, now_iso  # noqa: E402
from agent_pipeline.notify import Hook, build_message, emit  # noqa: E402
from agent_pipeline.report import build_view, find_ledgers, render_page  # noqa: E402
from agent_pipeline.spec import SpecError  # noqa: E402


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def pipeline(self, yaml_text: str):
        path = write(self.root / "pipelines" / "p.yaml", yaml_text)
        return load_pipeline(path, load_blocks(PACKAGE_ROOT / "blocks"))


class TestNotifySpec(Base):
    def test_bare_on_key_survives_yamls_boolean_trap(self) -> None:
        # YAML 1.1 reads `on:` as True; the parser has to see through that.
        p = self.pipeline("""
            name: p
            notify:
              - on: [phase_completed]
                run: echo hi
            phases:
              - id: a
                artifact: "{workdir}/a.md"
        """)
        self.assertEqual(len(p.notify), 1)
        self.assertEqual(p.notify[0].on, ("phase_completed",))

    def test_events_is_the_canonical_spelling(self) -> None:
        p = self.pipeline("""
            name: p
            notify:
              - events: [phase_blocked, run_shipped]
                run: echo hi
            phases:
              - id: a
                artifact: "{workdir}/a.md"
        """)
        self.assertEqual(p.notify[0].on, ("phase_blocked", "run_shipped"))

    def test_unknown_event_is_rejected_with_the_valid_list(self) -> None:
        with self.assertRaises(SpecError) as cm:
            self.pipeline("""
                name: p
                notify:
                  - events: [phase_exploded]
                    run: echo hi
                phases:
                  - id: a
                    artifact: "{workdir}/a.md"
            """)
        self.assertIn("phase_exploded", str(cm.exception))
        self.assertIn("phase_completed", str(cm.exception))


class TestNotifyEmit(Base):
    def hook(self, *events: str, run: str) -> list[Hook]:
        return [Hook(on=tuple(events), run=run, name="test")]

    def test_it_fires_only_on_matching_events(self) -> None:
        out = self.root / "hit.txt"
        hooks = self.hook("phase_completed", run=f"/bin/sh -c 'echo x >> {out}'")
        emit(hooks, "phase_started", pipeline="p", run="r", root=self.root)
        self.assertFalse(out.exists())
        emit(hooks, "phase_completed", pipeline="p", run="r", root=self.root)
        self.assertTrue(out.exists())

    def test_a_broken_hook_never_raises(self) -> None:
        hooks = self.hook("run_shipped", run="/definitely/not/a/command")
        result = emit(hooks, "run_shipped", pipeline="p", run="r", root=self.root)
        self.assertEqual(result.fired, [])
        self.assertEqual(len(result.failed), 1)
        self.assertIn("not found", result.failed[0][1])

    def test_a_nonzero_hook_is_recorded_as_failed_not_raised(self) -> None:
        hooks = self.hook("run_shipped", run="/bin/sh -c 'exit 3'")
        result = emit(hooks, "run_shipped", pipeline="p", run="r", root=self.root)
        self.assertEqual(len(result.failed), 1)

    def test_payload_reaches_the_command_as_environment(self) -> None:
        out = self.root / "env.txt"
        hooks = self.hook(
            "approval_needed",
            run=f'/bin/sh -c \'printf "%s" "$AGENT_PIPELINE_MESSAGE" > {out}\'',
        )
        emit(hooks, "approval_needed", pipeline="blog", run="2026-08-27",
             root=self.root, phase="signoff", detail="nobody approved it")
        body = out.read_text()
        self.assertIn("blog", body)
        self.assertIn("signoff", body)
        self.assertIn("nobody approved it", body)

    def test_free_text_is_not_templatable_into_the_command_line(self) -> None:
        # Only event/phase/run/pipeline are available as {} fields. A hook that
        # reaches for the message body must fail loudly rather than splice
        # attacker-shaped text into a shell command.
        hooks = self.hook("phase_blocked", run="echo {message}")
        result = emit(hooks, "phase_blocked", pipeline="p", run="r",
                      root=self.root, detail="; rm -rf /")
        self.assertEqual(result.fired, [])
        self.assertIn("unknown template field", result.failed[0][1])

    def test_message_is_readable_without_any_formatting_code(self) -> None:
        msg = build_message("approval_needed", pipeline="blog", run="r1",
                            phase="signoff", detail="waiting on you")
        self.assertIn("needs you", msg)
        self.assertIn("signoff", msg)
        self.assertIn("waiting on you", msg)


class TestReport(Base):
    PIPE = """
        name: rep
        workdir: runs/{run}
        phases:
          - id: a
            artifact: "{workdir}/a.md"
          - id: b
            artifact: "{workdir}/b.md"
            depends_on: [a]
          - id: c
            artifact: "{workdir}/c.md"
            depends_on: [b]
    """

    def make(self):
        p = self.pipeline(self.PIPE)
        workdir = self.root / "runs" / "t1"
        ctx = Context(root=self.root, run="t1", workdir=workdir, engine=PACKAGE_ROOT)
        ledger = Ledger(pipeline="rep", run="t1", created_at=now_iso())
        return p, ledger, ctx, workdir

    def test_states_reflect_the_run(self) -> None:
        p, ledger, ctx, workdir = self.make()
        write(workdir / "a.md", "done\n")
        ledger.entry("a").status = "complete"
        ledger.entry("b").status = "active"
        view = build_view(p, ledger, ctx)
        self.assertEqual(view.states["a"], "done")
        self.assertEqual(view.states["b"], "active")
        # c cannot start: b is not complete
        self.assertEqual(view.states["c"], "blocked")

    def test_an_active_phase_shows_why_it_is_blocked(self) -> None:
        p, ledger, ctx, workdir = self.make()
        write(workdir / "a.md", "done\n")
        ledger.entry("a").status = "complete"
        ledger.entry("b").status = "active"   # artifact b.md never written
        view = build_view(p, ledger, ctx)
        self.assertIn("b", view.blockers)
        self.assertTrue(any("does not exist" in m for m in view.blockers["b"]))

    def test_report_never_mutates_the_ledger_it_describes(self) -> None:
        p, ledger, ctx, workdir = self.make()
        write(workdir / "a.md", "done\n")
        ledger.entry("a").status = "complete"
        ledger.entry("b").status = "active"
        build_view(p, ledger, ctx)
        self.assertEqual(ledger.entry("b").verdicts, {})

    def test_the_page_is_genuinely_self_contained(self) -> None:
        p, ledger, ctx, workdir = self.make()
        write(workdir / "a.md", "done\n")
        ledger.entry("a").status = "complete"
        html = render_page([build_view(p, ledger, ctx)])
        self.assertIsNone(re.search(r'(src|href)="https?://', html))
        self.assertNotIn("<script", html)
        self.assertIn("<svg", html)

    def test_ledgers_are_discovered_under_the_root(self) -> None:
        p, ledger, ctx, workdir = self.make()
        ledger.save(workdir)
        found = find_ledgers(self.root)
        self.assertEqual(len(found), 1)
        self.assertTrue(str(found[0]).endswith("runs/t1/.pipeline/ledger.json"))

    def test_run_vars_survive_a_save_load(self) -> None:
        # Without this a report generated later cannot resolve {slug} paths.
        p, ledger, ctx, workdir = self.make()
        ledger.vars = {"slug": "MedievalHarvest"}
        ledger.save(workdir)
        again = Ledger.load(workdir, "rep", "t1")
        self.assertEqual(again.vars["slug"], "MedievalHarvest")

    def test_empty_project_renders_without_crashing(self) -> None:
        html = render_page([])
        self.assertIn("No runs found", html)


if __name__ == "__main__":
    unittest.main()

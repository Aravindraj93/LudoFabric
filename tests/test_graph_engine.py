"""LangGraph engine, recovery, retrieval, budgets and quality gates. No Unity or paid model calls."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from gameagent.config import Config
from gameagent.graph import GraphEngine, WorkflowStopped, STAGES
from gameagent.llm import LLM, BudgetReached, JobCancelled
from gameagent.metrics import record, summarize
from gameagent.orchestrator import Orchestrator
from gameagent.recovery import RetryPolicy, classify, decide, fingerprint
from gameagent.retrieval import ProjectKnowledge
from gameagent.schemas import AgentOutput, FileWrite, Review, ReviewIssue
from gameagent.unity.runner import UnityResult


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def orch(self):
        cfg = Config()
        cfg.polish_rounds = 1
        return Orchestrator(cfg, self.root / "Game")


class RecoveryPolicyTests(unittest.TestCase):
    def test_classification(self):
        self.assertEqual(classify(BudgetReached("Configured AI request/time budget reached")), "budget")
        self.assertEqual(classify("Compilation still fails"), "compile")
        self.assertEqual(classify("Assets/A.cs(3,4): error CS0103: x"), "compile")
        self.assertEqual(classify("Unity has no valid Editor license"), "license")
        self.assertEqual(classify("codex exec timed out after 5s"), "transient")

    def test_fingerprint_ignores_positions_and_paths(self):
        a = fingerprint(r"Assets/A.cs(3,4): error CS0103 at C:\Temp\x1\log 12:00:01")
        b = fingerprint(r"Assets/A.cs(9,1): error CS0103 at C:\Temp\y2\log 13:10:11")
        self.assertEqual(a, b)
        self.assertNotEqual(a, fingerprint("Assets/B.cs(1,1): error CS0246"))

    def test_decisions_are_bounded(self):
        p = RetryPolicy(max_attempts=2, base_delay=1)
        self.assertEqual(decide("compile", "f1", 0, [], p).action, "retry")
        self.assertEqual(decide("compile", "f1", 1, ["f1"], p).action, "blocked")  # same failure again
        self.assertEqual(decide("compile", "f2", 2, ["f1"], p).action, "blocked")  # limit
        self.assertEqual(decide("budget", "f", 0, [], p).action, "paused")
        self.assertEqual(decide("license", "f", 0, [], p).action, "blocked")
        self.assertEqual(decide("transient", "f", 0, [], p).delay, 1)


class GraphEngineTests(Base):
    def engine(self, orch, **fns):
        eng = GraphEngine(orch, RetryPolicy(max_attempts=2, base_delay=0), sleep=lambda s: None)
        noop = lambda: None
        eng.stage_fns = {s: fns.get(s, noop) for s in STAGES}
        orch.assets = Mock()
        return eng

    def test_all_stages_run_in_order(self):
        orch, calls = self.orch(), []
        eng = self.engine(orch, **{s: (lambda s=s: calls.append(s)) for s in STAGES})
        out = eng.run("coins")
        self.assertEqual(calls, STAGES)
        self.assertEqual(out["outcome"], "completed")
        self.assertTrue((orch.state_dir / "graph_checkpoints.sqlite").exists())

    def test_design_mode_stops_after_plan(self):
        orch, calls = self.orch(), []
        eng = self.engine(orch, **{s: (lambda s=s: calls.append(s)) for s in STAGES})
        self.assertEqual(eng.run("coins", stop_after="planned")["outcome"], "planned")
        self.assertEqual(calls, ["design", "plan"])

    def test_transient_failure_retried_then_succeeds(self):
        orch, n = self.orch(), {"build": 0}
        def build():
            n["build"] += 1
            if n["build"] == 1:
                raise RuntimeError("transport closed")
        out = self.engine(orch, build=build).run("coins")
        self.assertEqual(out["outcome"], "completed")
        self.assertEqual(n["build"], 2)

    def test_repeated_identical_failure_blocks_without_infinite_loop(self):
        orch, n = self.orch(), {"c": 0}
        def build():
            n["c"] += 1
            raise RuntimeError("Compilation still fails. Assets/A.cs(1,1): error CS0103")
        with self.assertRaises(WorkflowStopped) as ctx:
            self.engine(orch, build=build).run("coins")
        self.assertEqual(ctx.exception.outcome, "blocked")
        self.assertEqual(n["c"], 2)
        events = [json.loads(l) for l in (orch.state_dir / "events.jsonl").read_text().splitlines()]
        self.assertTrue(any(e["kind"] == "recovery" and e["action"] == "blocked" for e in events))

    def test_scenario_f_budget_pauses_and_keeps_exception_contract(self):
        orch = self.orch()
        def plan():
            raise BudgetReached("Configured AI request/time budget reached.")
        with self.assertRaises(BudgetReached):
            self.engine(orch, plan=plan).run("coins")
        self.assertEqual(json.loads(orch.state_file.read_text())["request"], "coins")

    def test_scenario_c_resume_does_not_repeat_completed_stages(self):
        calls = []
        def fake_design(self):
            calls.append("design"); self.state.phase = "designed"; self._save()
        def crash_plan(self):
            raise KeyboardInterrupt  # simulated process kill mid-workflow
        with patch.object(Orchestrator, "_design", fake_design), patch.object(Orchestrator, "_plan", crash_plan):
            orch = self.orch()
            with self.assertRaises(KeyboardInterrupt):
                GraphEngine(orch).run("coins", stop_after="planned")
        def ok_plan(self):
            calls.append("plan"); self.state.phase = "planned"; self._save()
        with patch.object(Orchestrator, "_design", fake_design), patch.object(Orchestrator, "_plan", ok_plan):
            restarted = self.orch()  # new process: state loaded from disk
            out = GraphEngine(restarted).run(None, stop_after="planned")
        self.assertEqual(out["outcome"], "planned")
        self.assertEqual(calls, ["design", "plan"])  # design not repeated


class CompileRecoveryTests(Base):
    def test_scenario_b_compile_error_detected_debugged_and_recompiled(self):
        orch = self.orch()
        orch.project.write_file("Assets/Game/Player.cs", "class Player { void A() { undefinedThing(); } }")
        bad = UnityResult(0, "", [{"file": "Assets/Game/Player.cs", "line": "1", "col": "28",
                                   "code": "CS0103", "msg": "The name 'undefinedThing' does not exist"}])
        orch.project.compile = Mock(side_effect=[bad, UnityResult(0, "")])
        fixed = AgentOutput(files=[FileWrite(path="Assets/Game/Player.cs", content="class Player { void A() { } }")])
        orch.studio.fix = Mock(return_value=fixed)
        self.assertTrue(orch._compile_fix_loop())
        errors, hints = orch.studio.fix.call_args[0]
        self.assertIn("CS0103", errors)
        self.assertIn("Assets/Game/Player.cs", hints)  # targeted retrieval, not the whole project
        self.assertNotIn("undefinedThing", orch.project.read_file("Assets/Game/Player.cs"))
        self.assertTrue(orch.state.evidence["compiled"])
        memory = orch.knowledge.similar_fixes("error CS0103 undefinedThing does not exist")
        self.assertEqual(memory[0]["files"], ["Assets/Game/Player.cs"])


class RetrievalTests(Base):
    def test_incremental_index_and_search(self):
        (self.root / "Assets").mkdir()
        (self.root / "Assets/Coin.cs").write_text("public class CoinPickup { public void Collect() {} }")
        (self.root / "Assets/Ui.cs").write_text("public class ScoreLabel { }")
        kb = ProjectKnowledge(self.root / "kb.sqlite")
        self.assertEqual(kb.index(self.root, ["Assets/Coin.cs", "Assets/Ui.cs"])["changed"], 2)
        self.assertEqual(kb.index(self.root, ["Assets/Coin.cs", "Assets/Ui.cs"])["changed"], 0)
        self.assertEqual(kb.search("CoinPickup Collect")[0]["path"], "Assets/Coin.cs")
        self.assertEqual(kb.index(self.root, ["Assets/Coin.cs"])["removed"], 1)


class BudgetAndUsageTests(Base):
    def llm(self, cfg):
        llm = LLM(cfg)
        llm.set_log_dir(self.root / "llm_logs")
        llm.backend = Mock()
        llm.backend.call.return_value = "ok"
        return llm

    def test_project_token_budget_survives_restart(self):
        cfg = Config(); cfg.llm_backend = "codex"; cfg.max_project_tokens = 100
        record(self.root / "metrics.jsonl", kind="llm", name="gameplay_t1", status="ok", model="m",
               usage={"input_tokens": 90, "output_tokens": 20})
        llm = self.llm(cfg)
        with self.assertRaises(BudgetReached):
            llm.complete(model="m", instructions="i", prompt="p")
        llm.backend.call.assert_not_called()
        self.assertEqual(json.loads((self.root / "pending_call.json").read_text())["status"], "budget_paused")

    def test_cancel_flag_stops_before_request(self):
        cfg = Config(); cfg.llm_backend = "codex"
        llm = self.llm(cfg)
        (self.root / "CANCEL").write_text("1")
        with self.assertRaises(JobCancelled):
            llm.complete(model="m", instructions="i", prompt="p")
        llm.backend.call.assert_not_called()

    def test_usage_per_agent_cached_retry_and_user_pricing(self):
        path = self.root / "metrics.jsonl"
        record(path, kind="llm", name="gameplay_t1", status="ok", model="m",
               usage={"input_tokens": 1000, "output_tokens": 100, "input_tokens_details": {"cached_tokens": 400}})
        record(path, kind="llm", name="fixer", status="ok", model="m", retry=True,
               usage={"prompt_tokens": 50, "completion_tokens": 5})
        with patch.dict("os.environ", {"LLM_PRICING_JSON": '{"m":[1.0,2.0,0.5]}'}):
            s = summarize(path)
        self.assertEqual(s["tokens"]["input"], 1050)
        self.assertEqual(s["tokens"]["cached"], 400)
        self.assertEqual(s["tokens"]["retry_input"], 50)
        self.assertEqual(s["per_agent"]["gameplay"]["calls"], 1)
        self.assertAlmostEqual(s["estimated_cost_usd"], (600 + 200 + 200 + 50 + 10) / 1e6)
        with patch.dict("os.environ", {"LLM_PRICING_JSON": ""}):
            self.assertIsNone(summarize(path)["estimated_cost_usd"])  # never invented


class VisualGateTests(Base):
    def shots(self, black: bool):
        from PIL import Image
        import random
        rnd = random.Random(1)
        out = []
        for i in range(2):
            img = Image.new("RGB", (320, 180), (0, 0, 0))
            if not black:
                img.putdata([(rnd.randrange(40, 220),) * 3 for _ in range(320 * 180)])
            p = self.root / f"shot{i}_{black}.png"
            img.save(p)
            out.append(p)
        return out

    def test_scenario_g_black_frame_detected_corrected_and_revalidated(self):
        orch = self.orch()
        orch.state.design = Mock(progression=[])
        orch.state.plan = Mock(tasks=[])
        orch.assets = Mock(describe_for_agents=Mock(return_value=""))
        orch.project.screenshots = Mock(side_effect=[(UnityResult(0, ""), self.shots(True)),
                                                     (UnityResult(0, ""), self.shots(False))])
        orch.studio = Mock()
        orch.studio.brief.return_value = Mock(prompt="fix", relevant_files=[])
        orch.studio.execute.return_value = AgentOutput(files=[FileWrite(path="Assets/Light.cs", content="//")])
        orch.studio.review.return_value = Review(score=9, summary="good")
        orch._run_builders_with_fixes = Mock(return_value=True)
        orch.cfg.polish_rounds = 2
        orch._polish()
        # round 1: deterministic gate caught it, no vision call, graphics agent assigned
        self.assertEqual(orch.studio.execute.call_args[0][0], "graphics")
        self.assertEqual(orch.state.reviews[0]["summary"], "Deterministic visual gate failed before model review.")
        # round 2: gate passed, vision review ran, defect resolved
        orch.studio.review.assert_called_once()
        self.assertTrue(orch.state.evidence["visual_gate_ok"])

    def test_maturity_requires_evidence(self):
        from gameagent.quality import assess_maturity
        self.assertEqual(assess_maturity("shipped", {})["level"], "none")
        self.assertEqual(assess_maturity("built", {"compiled": True, "builders_ok": True})["level"], "technical_prototype")
        full = {"compiled": True, "builders_ok": True, "visual_gate_ok": True, "tests_EditMode": True,
                "tests_PlayMode": True, "build_ok": True, "smoke_ok": True}
        self.assertEqual(assess_maturity("shipped", full)["level"], "playable_vertical_slice")


if __name__ == "__main__":
    unittest.main()

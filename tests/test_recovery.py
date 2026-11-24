"""Regression coverage for failures, restarts and quota pauses; no paid model calls."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from gameagent.config import Config
from gameagent.llm import LLM, UsageLimitReached, _CodexCLI, _UsageLimit, FatalLLMError
from gameagent.orchestrator import Orchestrator
from gameagent.schemas import AgentOutput, FileWrite, TaskSpec
from gameagent.state import State, atomic_json
from gameagent.unity.runner import UnityResult


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def llm(self):
        cfg = Config()
        cfg.llm_backend = "codex"
        cfg.codex_wait_on_limit = False
        llm = LLM(cfg)
        llm.set_log_dir(self.root / "llm_logs")
        llm.backend = Mock()
        return llm

    def test_atomic_checkpoint_survives_replacement_failure(self):
        path = self.root / "state.json"
        State(request="keep me").save(path)
        with patch("gameagent.state.os.replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                State(request="new").save(path)
        self.assertEqual(State.load(path).request, "keep me")

    def test_request_saved_before_first_model_failure(self):
        orch = Orchestrator(Config(), self.root / "Game")
        orch.studio.design = Mock(side_effect=RuntimeError("offline"))
        with self.assertRaises(RuntimeError):
            orch.run("collect coins")
        self.assertEqual(State.load(orch.state_file).request, "collect coins")

    def test_quota_pause_persists_request(self):
        llm = self.llm()
        llm.backend.call.side_effect = _UsageLimit("usage limit reached")
        with self.assertRaises(UsageLimitReached) as result:
            llm.complete(model="", instructions="role", prompt="task")
        pending = json.loads((self.root / "pending_call.json").read_text())
        self.assertEqual(pending["status"], "waiting_for_limit")
        self.assertEqual(pending["request"]["prompt"], "task")
        self.assertEqual(pending["retry_at"], result.exception.retry_at)
        self.assertEqual(llm.backend.call.call_count, 1)

    def test_answer_reused_after_process_restart(self):
        llm = self.llm()
        llm.backend.call.return_value = "answer"
        self.assertEqual(llm.complete(model="", instructions="role", prompt="task"), "answer")
        restarted = self.llm()
        self.assertEqual(restarted.complete(model="", instructions="role", prompt="task"), "answer")
        restarted.backend.call.assert_not_called()

    def test_codex_does_not_inherit_app_configuration(self):
        cfg = Config()
        cfg.codex_bin = "codex"
        backend = _CodexCLI(cfg)
        backend.workdir = self.root
        def run(cmd, **kwargs):
            self.assertIn("--ignore-user-config", cmd)
            self.assertIn("read-only", cmd)
            Path(cmd[cmd.index("-o") + 1]).write_text("OK")
            return Mock(returncode=0)
        with patch("gameagent.llm.subprocess.run", side_effect=run):
            self.assertEqual(backend.call("", "role", "task", [], False), "OK")

    def test_unsupported_model_fails_without_transient_retries(self):
        cfg = Config()
        cfg.codex_bin = "codex"
        backend = _CodexCLI(cfg)
        backend.workdir = self.root
        proc = Mock(returncode=1, stdout="", stderr='ERROR: {"code":"model_not_found"}')
        with patch("gameagent.llm.subprocess.run", return_value=proc):
            with self.assertRaises(FatalLLMError):
                backend.call("bad-model", "role", "task", [], False)

    def test_interrupted_task_replays_journal_without_model_call(self):
        orch = Orchestrator(Config(), self.root / "Game")
        task = TaskSpec(id="collect", role="gameplay", title="Coins", description="Collect")
        import hashlib
        key = hashlib.sha256(task.id.encode()).hexdigest()
        output = AgentOutput(files=[FileWrite(path="Assets/Coin.cs", content="// recovered")])
        atomic_json(orch.state_dir / "task_outputs" / f"{key}.json", output.model_dump())
        orch.studio = Mock()
        orch._do_task(task)
        orch.studio.execute.assert_not_called()
        self.assertEqual(task.status, "done")
        self.assertEqual((orch.project_path / "Assets/Coin.cs").read_text(), "// recovered")

    def test_dashboard_status_survives_restart(self):
        from webapp import server
        with patch.object(server, "STUDIO_DIR", self.root):
            atomic_json(self.root / "job.json", {"running": True, "project": "Game"})
            restarted = server.JobRunner()
            self.assertFalse(restarted.status["running"])
            self.assertEqual(restarted.status["state"], "interrupted")

    def test_import_project_path_survives_restart(self):
        from webapp import server
        path = self.root / "external" / "Game"
        State(request="coins").save(server.state_path(path))
        with patch.object(server, "STUDIO_DIR", self.root / "studio"), \
             patch.object(server, "projects_root", return_value=self.root / "default"), \
             patch.object(server.runner, "busy", return_value=False):
            result = server.import_project(server.ImportProject(path=str(path)))
            self.assertEqual(result["project"], "Game")
            self.assertEqual(server.project_path("Game"), path.resolve())
            self.assertEqual(server.project("Game")["state"]["request"], "coins")

    def test_failed_task_stops_pipeline_without_marking_built(self):
        orch = Orchestrator(Config(), self.root / "Game")
        task = TaskSpec(id="collect", role="gameplay", title="Coins", description="Collect")
        orch.state.plan = Mock(tasks=[task])
        orch._save = Mock()
        orch._do_task = Mock(side_effect=RuntimeError("transport closed"))
        orch._run_builders_with_fixes = Mock()
        with self.assertRaises(RuntimeError):
            orch._build_tasks()
        self.assertEqual(task.status, "failed")
        self.assertEqual(orch.state.phase, "new")
        orch._run_builders_with_fixes.assert_not_called()

    def test_nonzero_unity_exit_is_failure_even_with_bridge_result(self):
        self.assertFalse(UnityResult(1, "crashed", bridge={"ok": True}).ok)


if __name__ == "__main__":
    unittest.main()

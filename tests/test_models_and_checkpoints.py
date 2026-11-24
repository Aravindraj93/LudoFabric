import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from gameagent.config import Config
from gameagent.state import State
from gameagent.llm import _cli_error_summary
from fastapi import HTTPException
from webapp import server


class CheckpointTests(unittest.TestCase):
    def test_transient_permission_error_retries_without_losing_checkpoint(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            State(request="old").save(path)
            original = os.replace
            calls = []
            def replace(src, dst):
                calls.append(src)
                if len(calls) < 3:
                    raise PermissionError("file in use")
                return original(src, dst)
            with patch("gameagent.state.os.replace", side_effect=replace), patch("gameagent.state.time.sleep"):
                State(request="new").save(path)
            self.assertEqual(State.load(path).request, "new")
            self.assertEqual(len(calls), 3)

    def test_persistent_lock_preserves_both_versions(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            State(request="old").save(path)
            with patch("gameagent.state.os.replace", side_effect=PermissionError("locked")), \
                 patch("gameagent.state.time.sleep"):
                with self.assertRaisesRegex(PermissionError, "new data preserved"):
                    State(request="new").save(path)
            self.assertEqual(State.load(path).request, "old")
            recovery = list(Path(tmp).glob("*.tmp"))
            self.assertEqual(len(recovery), 1)
            self.assertEqual(json.loads(recovery[0].read_text())["request"], "new")


class ModelSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patcher = patch.object(server, "STUDIO_DIR", Path(self.tmp.name))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_provider_choices_are_saved_separately_and_reloaded(self):
        with patch.object(server, "save_env"), patch.object(server, "get_settings", return_value={}):
            for pid in ("codex", "claude", "gemini", "qwen", "openai", "chat"):
                server.post_settings(server.Settings(llm_backend=pid, model_coder=f"{pid}-choice"))
            for pid in ("codex", "claude", "gemini", "qwen", "openai", "chat"):
                self.assertEqual(server.provider_models(pid)["model_coder"], f"{pid}-choice")

    def test_job_override_applies_to_all_roles_for_all_providers(self):
        for pid in ("codex", "claude", "gemini", "qwen", "openai", "chat"):
            cfg = Config()
            server.apply_models(cfg, pid, model="chosen-model")
            self.assertTrue(all(getattr(cfg, key) == "chosen-model" for key in server.MODEL_FIELDS))

    def test_role_choices_are_not_replaced_by_empty_common_override(self):
        cfg = Config()
        server.apply_models(cfg, "claude", {"model_orchestrator": "plan", "model_coder": "code", "model_vision": "vision"})
        self.assertEqual((cfg.model_orchestrator, cfg.model_coder, cfg.model_vision), ("plan", "code", "vision"))

    def test_tests_forward_selected_model_for_every_provider(self):
        for pid in ("codex", "claude", "gemini", "qwen", "openai", "chat"):
            with self.subTest(provider=pid), patch("gameagent.llm.LLM") as llm, \
                 patch.object(server, "_provider") as provider:
                llm.return_value.complete.return_value = "GAMEAGENT_OK"
                provider.return_value.info.return_value = {"id": pid}
                result = server.test(pid, server.ModelTest(model="selected-model"))
                self.assertTrue(result["ok"])
                self.assertEqual(llm.return_value.complete.call_args.kwargs["model"], "selected-model")
                self.assertEqual(llm.call_args.args[0].llm_backend, pid)

    def test_rejected_model_cannot_start_or_modify_game_job(self):
        with patch.object(server, "projects_root", return_value=Path(self.tmp.name)), \
             patch.object(server.runner, "busy", return_value=False), \
             patch.object(server.runner, "start") as start, \
             patch.object(server, "_model_access", {}), \
             patch.object(server, "test", return_value={"ok": False, "output": "model not supported"}):
            with self.assertRaises(HTTPException) as result:
                server.start_job(server.JobRequest(request="coins", project="Game", mode="design", model="bad-model"))
            self.assertEqual(result.exception.status_code, 400)
            self.assertIn("progress is unchanged", result.exception.detail)
            start.assert_not_called()
            self.assertFalse((Path(self.tmp.name) / "Game.agent/state.json").exists())

    def test_shared_model_is_preflighted_only_once(self):
        with patch.object(server, "projects_root", return_value=Path(self.tmp.name)), \
             patch.object(server.runner, "busy", return_value=False), \
             patch.object(server.runner, "start") as start, \
             patch.object(server, "_model_access", {}), \
             patch.object(server, "test", return_value={"ok": True}) as test:
            server.start_job(server.JobRequest(request="coins", project="Game", mode="design", model="available-model"))
            self.assertEqual(test.call_count, 1)
            start.assert_called_once()

    def test_known_rejected_model_is_removed_from_cache_suggestions(self):
        root = Path(self.tmp.name)
        (root / "models_cache.json").write_text(json.dumps({"models": [{"slug": "bad-model"}, {"slug": "good-model"}]}))
        (root / "job.json").write_text(json.dumps({"provider": "codex", "error": "The 'bad-model' model is not supported"}))
        with patch.dict(os.environ, {"CODEX_HOME": str(root)}), patch.object(server, "_model_access", {}):
            self.assertEqual([m["id"] for m in server.model_choices("codex")["models"]], ["good-model"])
            (root / "job.json").write_text(json.dumps({"provider": "codex", "error": None}))
            self.assertEqual([m["id"] for m in server.model_choices("codex")["models"]], ["good-model"])

    def test_provider_error_does_not_echo_full_game_prompt(self):
        diagnostic = 'PRIVATE GAME PROMPT\n' * 100 + 'ERROR: {"error":{"message":"Model unavailable","code":"model_not_found"}}'
        self.assertEqual(_cli_error_summary(diagnostic), "Model unavailable")


if __name__ == "__main__":
    unittest.main()

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from gameagent.config import Config
from gameagent.state import State
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


if __name__ == "__main__":
    unittest.main()

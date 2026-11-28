"""Fail-closed checks for test reports and player smoke evidence; Unity execution is mocked."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from gameagent.config import Config
from gameagent.unity.project import UnityProject
from gameagent.unity.runner import UnityResult, read_log


class ValidationEvidenceTests(unittest.TestCase):
    def test_oversized_unity_log_is_bounded_and_cannot_pass_validation(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'large.log'
            path.write_bytes(b'HEAD' + b'x'*200 + b'TAIL')
            with patch('gameagent.unity.runner.MAX_CAPTURE_BYTES',64):
                text,truncated=read_log(path)
            self.assertTrue(truncated)
            self.assertTrue(text.startswith('HEAD')); self.assertTrue(text.endswith('TAIL'))
            result=UnityResult(0,text,bridge={'ok':True},log_truncated=truncated)
            self.assertFalse(result.ok)
            self.assertIn('Verification is incomplete',result.error_text())

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = UnityProject(Config(unity_editor=""), self.root / "Game", self.root / "state")

    def report(self, result):
        def run(*args, **kwargs):
            (self.root / "state/unity_logs/tests_EditMode.xml").write_text(
                f'<test-run result="Passed"><test-case result="{result}" /></test-run>')
            return UnityResult(0, "")
        self.project.runner.run = Mock(side_effect=run)
        return self.project.run_tests("EditMode")

    def test_zero_passed_and_inconclusive_tests_fail(self):
        self.assertFalse(self.report("Skipped")[0])
        self.assertFalse(self.report("Inconclusive")[0])
        self.assertTrue(self.report("Passed")[0])

    def smoke(self, early=None, log=None):
        process = Mock()
        process.poll.return_value = early
        def launch(*args, **kwargs):
            if log is not None:
                (self.root / "state/unity_logs/player_smoke.log").write_text(log)
            return process
        with patch("gameagent.unity.project.subprocess.Popen", side_effect=launch), \
             patch("gameagent.unity.project.time.sleep"):
            result = self.project.smoke_run(self.root / "Game.exe")
        process.wait.assert_called_once()
        return result

    def test_missing_smoke_log_is_not_success(self):
        ok, details = self.smoke()
        self.assertFalse(ok)
        self.assertIn("unverified", details)

    def test_early_exit_is_not_success_even_with_clean_log(self):
        self.assertFalse(self.smoke(early=0, log="Player initialized")[0])

    def test_observed_player_with_log_and_no_errors_passes(self):
        self.assertTrue(self.smoke(log="Player initialized")[0])

    def test_runtime_exception_fails(self):
        self.assertFalse(self.smoke(log="NullReferenceException: missing object")[0])

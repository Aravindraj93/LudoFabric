"""Platform selection, multi-target shipping, generated-code safety policy and dashboard request protection."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from gameagent.config import Config
from gameagent.orchestrator import Orchestrator
from gameagent.platforms import app_id, design_brief, module_installed, normalize, PLATFORMS
from gameagent.safety import check_files, scan
from gameagent.schemas import AgentOutput, FileWrite
from gameagent.unity.runner import UnityResult


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_normalize_and_aliases(self):
        self.assertEqual(normalize(None), ["windows"])
        self.assertEqual(normalize("pc, apk,iphone"), ["windows", "android", "ios"])
        self.assertEqual(normalize(["all"]), list(PLATFORMS))
        with self.assertRaises(ValueError):
            normalize(["playstation"])

    def test_module_detection(self):
        editor = self.root / "Editor" / "Unity.exe"
        (self.root / "Editor/Data/PlaybackEngines/AndroidPlayer").mkdir(parents=True)
        editor.write_text("")
        self.assertTrue(module_installed(str(editor), "android"))
        self.assertFalse(module_installed(str(editor), "ios"))
        self.assertIsNone(module_installed("", "android"))

    def test_mobile_design_brief_and_app_id(self):
        self.assertIn("touch", design_brief(["android"]))
        self.assertNotIn("touch", design_brief(["windows", "linux"]))
        self.assertEqual(app_id("Tide Garden!"), "com.ludofabric.tidegarden")

    def orch(self, targets):
        cfg = Config()
        cfg.unity_editor = ""
        o = Orchestrator(cfg, self.root / "Game")
        o.set_targets(targets)
        o.state.design = Mock(title="Tide Garden")
        o.project.run_tests = Mock(return_value=(True, "ok"))
        o._compile_fix_loop = Mock(return_value=True)
        o.project.smoke_run = Mock(return_value=(True, ""))
        def build(out, platform="windows", bundle=None):
            out.parent.mkdir(parents=True, exist_ok=True)
            out.mkdir() if platform in ("ios", "webgl") else out.write_text("bin")
            return UnityResult(0, "", bridge={"ok": True})
        o.project.build = Mock(side_effect=build)
        return o

    def test_ships_every_chosen_platform(self):
        o = self.orch(["windows", "android", "ios"])
        self.assertIn("touch", o.studio.platform_note)
        o._ship()
        builds = o.state.evidence["builds"]
        self.assertEqual({t for t, b in builds.items() if b["ok"]}, {"windows", "android", "ios"})
        self.assertTrue(builds["android"]["artifact"].endswith("TideGarden.apk"))
        self.assertIn("Xcode", builds["ios"]["note"])
        self.assertEqual(o.project.build.call_args_list[1].args[1:], ("android", "com.ludofabric.tidegarden"))
        o.project.smoke_run.assert_called_once()  # only Windows can be run on this host
        self.assertEqual(o.state.phase, "shipped")

    def test_missing_module_reported_and_successful_builds_kept_on_resume(self):
        o = self.orch(["windows", "android"])
        with patch("gameagent.platforms.module_installed", side_effect=lambda e, p: p != "android"):
            with self.assertRaises(RuntimeError) as ctx:
                o._ship()
        self.assertIn("android (module_missing)", str(ctx.exception))
        self.assertNotEqual(o.state.phase, "shipped")
        self.assertTrue(o.state.evidence["builds"]["windows"]["ok"])
        o.project.build.reset_mock()
        o._ship()  # module installed now: only Android is built
        self.assertEqual([c.args[1] for c in o.project.build.call_args_list], ["android"])
        self.assertEqual(o.state.phase, "shipped")

    def test_adding_platform_to_shipped_game_rebuilds_only(self):
        o = self.orch(None)
        o.state.phase = "shipped"
        o.set_targets(["windows", "android"])
        self.assertEqual(o.state.phase, "polished")
        o.set_targets(["windows"])  # removing a platform never forces work
        self.assertEqual(o.state.phase, "polished")


class SafetyTests(unittest.TestCase):
    def test_dangerous_apis_rejected(self):
        bad = {
            "Assets/Editor/A.cs": 'using UnityEditor; class A { void B() { System.Diagnostics.Process.Start("cmd.exe"); } }',
            "Assets/Game/R.cs": "class R { [System.Runtime.InteropServices.DllImport(\"x\")] static extern void F(); }",
            "Assets/Editor/D.cs": 'using UnityEditor; class D { void E() { System.IO.Directory.Delete(@"C:\\Users", true); } }',
            "Assets/Editor/N.cs": "using UnityEditor; class N { System.Net.Http.HttpClient c; }",
            "Assets/Game/P.cs": 'class P { void Q() { System.IO.File.WriteAllText("../../x.txt", ""); } }',
        }
        for path, src in bad.items():
            self.assertTrue(scan(path, src), path)
        with self.assertRaises(ValueError):
            check_files(bad)

    def test_normal_game_code_allowed(self):
        ok = {
            "Assets/Game/Save.cs": "using UnityEngine; class Save { void S() { System.IO.File.WriteAllText("
                                   "Application.persistentDataPath + \"/s.json\", \"{}\"); } }",
            "Assets/Game/Net.cs": "using UnityEngine.Networking; class Net { UnityWebRequest r; }",  # runtime only
            "Assets/Editor/B.cs": "// never use Process.Start here\nusing UnityEditor; class B { }",
        }
        self.assertEqual(check_files(ok), [])

    def test_policy_modes(self):
        bad = {"Assets/Game/X.cs": "class X { void Y() { System.Diagnostics.Process.Start(\"a\"); } }"}
        with patch.dict("os.environ", {"CODE_POLICY": "warn"}):
            self.assertEqual(len(check_files(bad)), 1)
        with patch.dict("os.environ", {"CODE_POLICY": "off"}):
            self.assertEqual(check_files(bad), [])

    def test_model_output_with_violation_is_rejected_before_writing(self):
        with TemporaryDirectory() as tmp:
            o = Orchestrator(Config(), Path(tmp) / "Game")
            out = AgentOutput(files=[FileWrite(path="Assets/Editor/X.cs",
                              content="using UnityEditor; class X { void Y() { System.Diagnostics.Process.Start(\"a\"); } }")])
            with self.assertRaises(ValueError):
                o.studio._validate_output(out, {})
            self.assertFalse((Path(tmp) / "Game/Assets/Editor/X.cs").exists())


class DashboardProtectionTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from webapp import server
        self.server = server
        self.client = TestClient(server.app, base_url="http://127.0.0.1:8765")

    def test_token_embedded_and_required_for_state_changes(self):
        page = self.client.get("/").text
        self.assertIn(self.server.SESSION_TOKEN, page)
        self.assertEqual(self.client.post("/api/jobs/cancel").status_code, 403)
        r = self.client.post("/api/jobs/cancel", headers={"X-LudoFabric-Token": self.server.SESSION_TOKEN})
        self.assertEqual(r.status_code, 409)  # authorized; simply no running job

    def test_cross_site_and_dns_rebinding_rejected(self):
        h = {"X-LudoFabric-Token": self.server.SESSION_TOKEN}
        self.assertEqual(self.client.post("/api/jobs/cancel", headers={**h, "Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/jobs/current", headers={"Host": "evil.example:8765"}).status_code, 403)
        self.assertEqual(self.client.get("/api/jobs/current").status_code, 200)


if __name__ == "__main__":
    unittest.main()

"""Asset workflow regressions; no network downloads, Unity or paid inference."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx

from gameagent.assets.manager import AssetManager, AssetRecord, sources_note
from gameagent.assets.sketchfab import Sketchfab, allowed_licenses
from gameagent.change import ChangeRunner, ChangePlan
from gameagent.config import Config
from gameagent.orchestrator import Orchestrator
from gameagent.schemas import AgentOutput, AssetRequest, Brief, TaskSpec
from tests.test_change import make_unity_project


class AssetWorkflowTests(unittest.TestCase):
    def test_platform_row_is_below_actions(self):
        html = (Path(__file__).resolve().parents[1] / "webapp/static/studio.html").read_text(encoding="utf-8")
        self.assertLess(html.index('onclick="openProjectPath()"'), html.index('class="platform-row"'))
        self.assertEqual(html.count('id="platforms"'), 1)

    def test_planner_sees_configured_sources(self):
        cfg = Config(sketchfab_token="")
        self.assertIn("DISABLED", sources_note(cfg))
        cfg.sketchfab_token = "test-only-token"
        with patch.dict("os.environ", {"ALLOW_SHAREALIKE_ASSETS": "0"}):
            note = sources_note(cfg, gltf_supported=False)
            self.assertIn("ENABLED", note)
            self.assertIn("ONLY kind=hdri", note)
            self.assertNotIn("CC-BY-SA", note)

    def test_licence_filter_is_explicit(self):
        response = Mock()
        response.json.return_value = {"results": [
            {"uid": slug, "license": {"slug": slug}} for slug in ("cc0", "by", "by-sa", "by-nc", "")
        ]}
        client = Mock(get=Mock(return_value=response))
        sf = Sketchfab(client, "test", 10000)
        with patch.dict("os.environ", {"ALLOW_SHAREALIKE_ASSETS": "0"}):
            self.assertEqual([m["uid"] for m in sf.search("tree")], ["cc0", "by"])
        with patch.dict("os.environ", {"ALLOW_SHAREALIKE_ASSETS": "1"}):
            self.assertIn("by-sa", allowed_licenses())

    def test_source_failure_tries_next_source(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = AssetManager(Config(), root, root)
            self.addCleanup(manager.http.close)
            manager._try_polyhaven = Mock(side_effect=httpx.ConnectError("offline"))
            record = AssetRecord(key="tree", kind="model", query="tree", status="ok")
            manager._try_sketchfab = Mock(return_value=record)
            self.assertIs(manager._fetch_query(AssetRequest(key="tree", query="tree")), record)

    def test_procedural_change_cannot_download(self):
        with TemporaryDirectory() as tmp:
            path = make_unity_project(Path(tmp) / "Game")
            orch = Orchestrator(Config(unity_editor=""), path)
            runner = ChangeRunner(orch)
            orch.state = SimpleNamespace(design=SimpleNamespace(asset_policy="procedural_only"))
            orch.assets = Mock()
            plan = ChangePlan(summary="No downloads", tasks=[TaskSpec(
                id="one", role="graphics", title="Style", description="Style", outputs=["Assets/Style.cs"])],
                assets=[AssetRequest(key="tree", query="tree")])
            runner._fetch_assets(plan)
            orch.assets.fetch_all.assert_not_called()
            self.assertFalse(orch.studio.uses_assets)
            self.assertEqual(plan.assets, [])

    def test_external_asset_prompt_has_no_scenekit_dependencies(self):
        with TemporaryDirectory() as tmp:
            path = make_unity_project(Path(tmp) / "Game")
            orch = Orchestrator(Config(unity_editor=""), path)
            ChangeRunner(orch)
            orch.studio.uses_assets = True
            orch.llm.structured = Mock(return_value=AgentOutput())
            task = TaskSpec(id="dress", role="level", title="Dress", description="Dress", outputs=[])
            orch.studio.execute("level", Brief(prompt="Use the downloaded tree"), task)
            instructions = orch.llm.structured.call_args.kwargs["instructions"]
            self.assertNotIn("SceneKit.InstantiateModel", instructions)
            self.assertIn("PrefabUtility.InstantiatePrefab", instructions)
            self.assertIn("BUILTIN", instructions)


if __name__ == "__main__":
    unittest.main()

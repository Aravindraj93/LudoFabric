from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
import io
import tarfile

from gameagent.cli import find_cli
from gameagent.config import Config
from gameagent.orchestrator import Orchestrator
from gameagent.schemas import AgentOutput
from gameagent.unity.project import UnityProject
from gameagent.unity.runner import UnityResult


class CliAndBuilderTests(unittest.TestCase):
    def test_explicit_cli_override_wins(self):
        with patch.dict('os.environ', {'CODEX_BIN': 'chosen.exe'}):
            self.assertEqual(find_cli('codex'), 'chosen.exe')

    def test_final_builder_repair_is_verified_and_bootstrap_runs_first(self):
        with TemporaryDirectory() as tmp:
            cfg = Config(); cfg.max_fix_attempts = 1
            orch = Orchestrator(cfg, Path(tmp) / 'Game')
            orch.state.builder_methods = ['Game.UI.Build', 'Game.TextMeshProBootstrap.Build']
            orch.project = Mock()
            orch.project.compile.return_value = UnityResult(0, '', bridge={'ok': True})
            orch.project.run_builders.side_effect = [
                UnityResult(0, '', bridge={'ok': False, 'errors': ['missing font']}),
                UnityResult(0, '', bridge={'ok': True})]
            orch.studio.fix = Mock(return_value=AgentOutput())
            orch._builder_files = Mock(return_value=[])
            self.assertTrue(orch._run_builders_with_fixes())
            self.assertEqual(orch.project.run_builders.call_count, 2)
            self.assertIn('TextMeshProBootstrap', orch.state.builder_methods[0])
            orch.studio.fix.assert_called_once()

    def test_tmp_archive_installs_assets_and_preserves_custom_settings(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = Config(); cfg.unity_editor = str(root / 'Editor/Unity.exe')
            project = UnityProject(cfg, root / 'Game', root / 'state')
            folder = project.editor_data / 'Resources/PackageManager/BuiltInPackages/com.unity.ugui/Package Resources'
            folder.mkdir(parents=True)
            with tarfile.open(folder / 'TMP Essential Resources.unitypackage', 'w:gz') as archive:
                for name, value in [('guid/pathname', b'Assets/TextMesh Pro/Resources/TMP Settings.asset'),
                                    ('guid/asset', b'standard'), ('guid/asset.meta', b'guid: test')]:
                    info = tarfile.TarInfo(name); info.size = len(value)
                    archive.addfile(info, io.BytesIO(value))
            project._install_tmp_resources()
            settings = project.path / 'Assets/TextMesh Pro/Resources/TMP Settings.asset'
            self.assertEqual(settings.read_bytes(), b'standard')
            self.assertTrue(settings.with_name(settings.name + '.meta').exists())
            settings.write_bytes(b'custom')
            project._install_tmp_resources()
            self.assertEqual(settings.read_bytes(), b'custom')


if __name__ == '__main__':
    unittest.main()

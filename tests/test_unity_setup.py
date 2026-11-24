import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from gameagent.config import Config
from gameagent.unity.project import UnityProject
from gameagent.unity.runner import UnityRunner


class UnitySetupTests(unittest.TestCase):
    def test_package_versions_follow_installed_editor_minimum(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = Config()
            cfg.unity_editor = str(root / "Editor/Unity.exe")
            project = UnityProject(cfg, root / "Game", root / "state")
            manifest = project.path / "Packages/manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"dependencies": {
                "com.unity.inputsystem": "1.11.2",
                "com.unity.timeline": "1.9.0",
                "com.unity.cinemachine": "3.1.2",
            }}))
            catalog = project.editor_data / "Resources/PackageManager/Editor/manifest.json"
            catalog.parent.mkdir(parents=True)
            catalog.write_text(json.dumps({"packages": {
                "com.unity.inputsystem": {"minimumVersion": "1.19.0", "version": "1.19.0"},
                "com.unity.timeline": {"minimumVersion": "1.8.12", "version": "1.8.12"},
                "com.unity.cloud.gltfast": {"minimumVersion": "6.14.1", "version": "6.14.1"},
            }}))
            project._configure_packages()
            deps = json.loads(manifest.read_text())["dependencies"]
            self.assertEqual(deps["com.unity.inputsystem"], "1.19.0")
            self.assertEqual(deps["com.unity.timeline"], "1.9.0")
            self.assertEqual(deps["com.unity.cloud.gltfast"], "6.14.1")
            self.assertEqual(deps["com.unity.cinemachine"], "3.1.7")
            for custom in ("https://example.com/custom.git", "2.10.7"):
                deps["com.unity.cinemachine"] = custom
                manifest.write_text(json.dumps({"dependencies": deps}))
                project._configure_packages()
                self.assertEqual(json.loads(manifest.read_text())["dependencies"]["com.unity.cinemachine"], custom)

    def test_compiler_error_does_not_capture_preceding_command_dump(self):
        log = ('## Command: csc.exe\n-r:Unity.dll\n## Output\n'
               'Library/PackageCache/input/Editor/Test.cs(22,41): error CS0619: obsolete\n')
        errors = UnityRunner._parse_errors(log + log)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["file"], "Library/PackageCache/input/Editor/Test.cs")


if __name__ == "__main__":
    unittest.main()

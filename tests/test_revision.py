import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from gameagent.config import Config
from gameagent.orchestrator import Orchestrator
from gameagent.revision import prepare, inspect, hashes, source_from_request, validate_source
from gameagent.schemas import Architecture, Plan, TaskSpec


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'Original'
        for name in ('Assets', 'Packages', 'ProjectSettings'):
            (self.source / name).mkdir(parents=True)
        (self.source / 'Packages/manifest.json').write_text('{"dependencies":{}}')
        (self.source / 'ProjectSettings/ProjectVersion.txt').write_text('m_EditorVersion: 6000.5.0f1')
        (self.source / 'ProjectSettings/EditorBuildSettings.asset').write_text('Assets/Main.unity')
        (self.source / 'Assets/Game.cs').write_text('public class Game {}')
        (self.source / 'Assets/Game.cs.meta').write_text('guid: original')
        (self.source / 'Library').mkdir()
        (self.source / 'Library/cache').write_text('exclude')
        self.dest = self.root / 'Revision'
        self.state = self.root / 'Revision.agent'

    def test_copies_authored_content_preserves_meta_and_original(self):
        before = hashes(self.source)
        record = prepare(self.source, self.dest, self.state)
        self.assertEqual(before, hashes(self.dest))
        self.assertTrue(Path(record['snapshot']).exists())
        self.assertFalse((self.dest / 'Library').exists())
        (self.dest / 'Assets/Game.cs').write_text('revision')
        self.assertEqual(hashes(self.source), before)
        self.assertEqual(prepare(self.source, self.dest, self.state), record)
        self.assertEqual((self.dest / 'Assets/Game.cs').read_text(), 'revision')

    def test_same_or_nested_destination_rejected(self):
        for dest in (self.source, self.source / 'Revision', self.root):
            with self.assertRaisesRegex(ValueError, 'non-nested'):
                validate_source(self.source, dest)

    def test_existing_destination_is_not_overwritten(self):
        self.dest.mkdir(); (self.dest / 'keep').write_text('manual work')
        with self.assertRaisesRegex(ValueError, 'not empty'):
            prepare(self.source, self.dest, self.state)
        self.assertEqual((self.dest / 'keep').read_text(), 'manual work')

    def test_inspection_contains_real_code_and_scene_settings(self):
        record = prepare(self.source, self.dest, self.state)
        context = inspect(self.dest, record)
        self.assertIn('public class Game', context)
        self.assertIn('Assets/Main.unity', context)
        self.assertIn('host performs these operations', context)

    def test_prompt_source_extraction(self):
        self.assertEqual(source_from_request('Improve the existing game at C:\\UnityProjects\\Coins. Preserve saves.'), r'C:\UnityProjects\Coins')
        self.assertEqual(source_from_request('Polish the existing game at "C:\\My Games\\Coins".'), r'C:\My Games\Coins')
        self.assertIsNone(source_from_request('Create a new game'))

    def test_local_packages_rejected_before_copying(self):
        (self.source / 'Packages/manifest.json').write_text('{"dependencies":{"custom":"file:../../local"}}')
        with self.assertRaisesRegex(ValueError, 'local file:'):
            prepare(self.source, self.dest, self.state)
        self.assertFalse(self.dest.exists())

    def test_copy_preserves_project_instructions_and_development_records(self):
        (self.source/'Docs').mkdir()
        (self.source/'Tests').mkdir()
        (self.source/'AGENTS.md').write_text('Preserve levels; hold expansion.')
        (self.source/'Docs/PROJECT_MEMORY.md').write_text('Guardian is the current milestone.')
        (self.source/'Tests/check.txt').write_text('existing validation harness')
        (self.source/'UserSettings').mkdir()
        (self.source/'UserSettings/Search.index').write_text('{"name":"Assets"}')
        prepare(self.source,self.dest,self.state)
        self.assertEqual((self.dest/'AGENTS.md').read_text(),'Preserve levels; hold expansion.')
        self.assertTrue((self.dest/'Docs/PROJECT_MEMORY.md').exists())
        self.assertTrue((self.dest/'Tests/check.txt').exists())
        self.assertEqual((self.dest/'UserSettings/Search.index').read_text(),'{"name":"Assets"}')

    def test_interrupted_copy_commit_can_resume(self):
        record = prepare(self.source, self.dest, self.state)
        record['ready'] = False
        (self.state / 'revision.json').write_text(json.dumps(record))
        self.assertTrue(prepare(self.source, self.dest, self.state)['ready'])

    def test_revision_planning_migrates_blocked_attempt_without_unity_or_old_plan(self):
        orch = Orchestrator(Config(), self.dest)
        orch.state.request = 'Polish graphics'
        orch.state.plan = Plan(architecture=Architecture(root_namespace='Game',overview='old',systems=[]),assets=[],
                               tasks=[TaskSpec(id='old', role='level', title='Blocked', description='copy', status='failed')])
        orch.state.phase = 'assets'; orch._save()
        self.dest.mkdir(); (self.dest / 'old-attempt.txt').write_text('preserved')
        def design():
            self.assertIsNone(orch.state.plan)
            self.assertIn('public class Game', orch.studio.revision_context)
            orch.state.phase = 'designed'
        orch._design = Mock(side_effect=design)
        orch._plan = Mock(side_effect=lambda: setattr(orch.state, 'phase', 'planned'))
        orch.project.ensure_created = Mock()
        orch.run(None, stop_after='planned', source=str(self.source))
        self.assertEqual(orch.state.revision_source, str(self.source))
        self.assertTrue(list(self.state.glob('previous-attempt-*/old-attempt.txt')))
        orch.project.ensure_created.assert_not_called()
        # A second Resume must preserve the revised files and completed planning.
        orch.run(None, stop_after='planned')
        self.assertEqual(orch._design.call_count, 1)


if __name__ == '__main__':
    unittest.main()

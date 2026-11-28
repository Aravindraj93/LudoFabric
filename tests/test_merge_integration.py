"""Regressions for the local-revision/LangGraph merge. No paid model calls."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from gameagent.config import Config
from gameagent.graph import GraphEngine, STAGES, WorkflowStopped, run_project
from gameagent.locking import project_lock
from gameagent.orchestrator import Orchestrator
from gameagent.recovery import RetryPolicy


class MergeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = Config(); self.cfg.engine = 'graph'

    def engine(self, orch, **stages):
        engine = GraphEngine(orch, RetryPolicy(max_attempts=2, base_delay=0), sleep=lambda _: None)
        engine.stage_fns = {s: stages.get(s, lambda: None) for s in STAGES}
        orch.assets = Mock()
        return engine

    def test_restart_retains_graph_identity_and_retry_history(self):
        path = self.root / 'Game'
        orch = Orchestrator(self.cfg, path)
        def failure():
            raise RuntimeError('transport closed')
        with self.assertRaises(WorkflowStopped):
            self.engine(orch, build=failure).run('coins')
        thread = orch.state.workflow['thread_id']
        first = json.loads(orch.state_file.read_text())['workflow']
        resumed = Orchestrator(self.cfg, path)
        engine = self.engine(resumed, build=failure)
        with self.assertRaises(WorkflowStopped):
            engine.run(None)
        self.assertEqual(resumed.state.workflow, first)
        from langgraph.checkpoint.sqlite import SqliteSaver
        with SqliteSaver.from_conn_string(str(orch.state_dir / 'graph_checkpoints.sqlite')) as saver:
            saved = engine.build(saver).get_state({'configurable': {'thread_id': thread}})
            self.assertEqual(saved.values['attempts']['build'], 3)
            self.assertEqual(len(saved.values['fingerprints']['build']), 3)

    def test_crash_resumes_pending_node_without_restarting_completed_nodes(self):
        path = self.root / 'Game'; calls = []
        def design(): calls.append('design')
        def crash(): raise KeyboardInterrupt()
        orch = Orchestrator(self.cfg, path)
        with self.assertRaises(KeyboardInterrupt):
            self.engine(orch, design=design, plan=crash).run('coins', 'planned')
        thread = orch.state.workflow['thread_id']
        resumed = Orchestrator(self.cfg, path)
        result = self.engine(resumed, design=design, plan=lambda: calls.append('plan')).run(None, 'planned')
        self.assertEqual(result['outcome'], 'planned')
        self.assertEqual(resumed.state.workflow['thread_id'], thread)
        self.assertEqual(calls, ['design', 'plan'])

    def test_graph_entry_forwards_revision_source_and_preserves_original(self):
        source = self.root / 'Original'
        for name in ('Assets', 'Packages', 'ProjectSettings'):
            (source / name).mkdir(parents=True)
        for rel, text in {'Packages/manifest.json':'{"dependencies":{}}',
                          'ProjectSettings/ProjectVersion.txt':'6000.5',
                          'ProjectSettings/EditorBuildSettings.asset':'Assets/Main.unity',
                          'Assets/Game.cs':'public class Game {}'}.items():
            (source / rel).write_text(text)
        def design(orch):
            self.assertIn('public class Game', orch.studio.revision_context)
            orch.state.phase = 'designed'; orch._save()
        def plan(orch): orch.state.phase = 'planned'; orch._save()
        with patch.object(Orchestrator, '_design', design), patch.object(Orchestrator, '_plan', plan):
            result = run_project(self.cfg, self.root/'Revision', 'Improve lighting', 'planned', targets=['windows','android'], source=str(source))
        self.assertEqual(result['outcome'], 'planned')
        self.assertEqual((source/'Assets/Game.cs').read_text(), 'public class Game {}')
        self.assertTrue((self.root/'Revision.agent/source-snapshot.zip').exists())
        saved = json.loads((self.root/'Revision.agent/state.json').read_text())
        self.assertEqual(saved['targets'], ['windows','android'])

    def test_second_process_cannot_mutate_locked_project(self):
        path = self.root/'Game'
        code = 'from pathlib import Path; from gameagent.locking import project_lock\nwith project_lock(Path(__import__("sys").argv[1])): print("acquired")'
        with project_lock(path):
            attempt = subprocess.run([sys.executable, '-c', code, str(path)], capture_output=True, text=True)
            self.assertNotEqual(attempt.returncode, 0)
            self.assertIn('already being modified', attempt.stderr)
        with project_lock(path):
            pass  # OS lock released after the first owner exits its context.

    def test_invalid_job_mode_rejected(self):
        from webapp.server import JobRequest
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            JobRequest(mode='unexpected')

    def test_existing_project_change_context_includes_instructions_and_latest_memory(self):
        from gameagent.change import ChangeRunner
        project=self.root/'Game'
        (project/'Assets').mkdir(parents=True)
        (project/'ProjectSettings').mkdir()
        (project/'Docs').mkdir()
        (project/'AGENTS.md').write_text('Do not create level eleven.')
        (project/'Docs/PROJECT_MEMORY.md').write_text('Old entry\n' * 5000 + 'Latest: Guardian benchmark only.')
        orch=Orchestrator(self.cfg,project)
        runner=ChangeRunner(orch)
        context=runner._context('Add an Editor audit')
        self.assertIn('Do not create level eleven.',context)
        self.assertIn('Latest: Guardian benchmark only.',context)
        self.assertLess(len(runner.project_notes),31000)

    def test_pause_contract_survives_crash_before_recovery(self):
        from gameagent.llm import BudgetReached
        orch = Orchestrator(self.cfg, self.root/'Game')
        def budget(): raise BudgetReached('Configured AI request/time budget reached.')
        engine = self.engine(orch, plan=budget)
        def crash(_): raise KeyboardInterrupt()
        engine._recover = crash
        with self.assertRaises(KeyboardInterrupt): engine.run('coins')
        resumed = Orchestrator(self.cfg, self.root/'Game')
        with self.assertRaises(BudgetReached): self.engine(resumed).run(None)

    def test_dashboard_worker_uses_captured_configuration(self):
        from webapp import server
        cfg = Config(); cfg.validate = Mock()
        job = server.JobRequest(request='coins', mode='design')
        with patch.object(server, 'STUDIO_DIR', self.root):
            worker = server.JobRunner()
        worker.persist = Mock()
        with patch('gameagent.graph.run_project') as run, patch.object(server, 'Config') as reconstruct:
            worker._run(job, self.root/'Game', cfg)
        reconstruct.assert_not_called()
        self.assertIs(run.call_args.args[0], cfg)
        self.assertEqual(worker.status['state'], 'completed')


if __name__ == '__main__': unittest.main()

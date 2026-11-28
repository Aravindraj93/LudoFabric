import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
from webapp import server


class JobNoticeTests(unittest.TestCase):
    def test_dismiss_preserves_failure_history_and_survives_restart(self):
        with TemporaryDirectory() as temp:
            folder=Path(temp)
            worker=Mock(status={'running':False,'project':'OldGame','state':'failed','error':'old failure'})
            worker.busy.return_value=False
            with patch.object(server,'STUDIO_DIR',folder), patch.object(server,'runner',worker):
                client=TestClient(server.app,base_url='http://127.0.0.1:8765')
                response=client.post('/api/jobs/dismiss',headers={'X-LudoFabric-Token':server.SESSION_TOKEN})
                self.assertEqual(response.status_code,200)
                saved=json.loads((folder/'job.json').read_text())
                self.assertEqual(saved['error'],'old failure')
                self.assertEqual(saved['state'],'failed')
                self.assertTrue(saved['notice_dismissed'])
                restored=server.JobRunner()
                self.assertTrue(restored.status['notice_dismissed'])
                self.assertEqual(restored.status['error'],'old failure')
                worker.persist.assert_not_called()

    def test_running_job_cannot_be_dismissed(self):
        worker=Mock(); worker.busy.return_value=True
        with patch.object(server,'runner',worker):
            client=TestClient(server.app,base_url='http://127.0.0.1:8765')
            response=client.post('/api/jobs/dismiss',headers={'X-LudoFabric-Token':server.SESSION_TOKEN})
        self.assertEqual(response.status_code,409)

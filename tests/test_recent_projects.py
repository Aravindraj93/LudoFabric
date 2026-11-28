import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
from gameagent.state import State, atomic_json
from webapp import server


class RecentProjectTests(unittest.TestCase):
    def test_reopening_remembers_full_path_without_duplicates(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); studio=root/'studio'; games=root/'games'; games.mkdir()
            project=games/'Demo'; project.mkdir()
            checkpoint=server.state_path(project)
            State(origin='external').save(checkpoint)
            original=checkpoint.read_bytes()
            atomic_json(studio/'projects.json',{'Demo':str(project)})
            worker=Mock(); worker.busy.return_value=False
            with patch.object(server,'STUDIO_DIR',studio), patch.object(server,'projects_root',return_value=games), patch.object(server,'runner',worker):
                client=TestClient(server.app,base_url='http://127.0.0.1:8765')
                for _ in range(2):
                    result=client.post('/api/projects/Demo/open',headers={'X-LudoFabric-Token':server.SESSION_TOKEN})
                    self.assertEqual(result.status_code,200)
                recent=server.recent_projects()
                self.assertEqual(len(recent),1)
                self.assertEqual(recent[0]['path'],str(project.resolve()))
                listed=client.get('/api/projects').json()[0]
                self.assertEqual(listed['path'],str(project.resolve()))
                self.assertGreater(listed['last_opened'],0)
                self.assertTrue(listed['available'])
                self.assertEqual(checkpoint.read_bytes(),original)

    def test_missing_project_cannot_be_opened(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); studio=root/'studio'; games=root/'games'; games.mkdir()
            missing=games/'Missing'
            State().save(server.state_path(missing))
            atomic_json(studio/'projects.json',{'Missing':str(missing)})
            worker=Mock(); worker.busy.return_value=False
            with patch.object(server,'STUDIO_DIR',studio), patch.object(server,'projects_root',return_value=games), patch.object(server,'runner',worker):
                client=TestClient(server.app,base_url='http://127.0.0.1:8765')
                response=client.post('/api/projects/Missing/open',headers={'X-LudoFabric-Token':server.SESSION_TOKEN})
                self.assertEqual(response.status_code,404)
                self.assertEqual(server.recent_projects(),[])

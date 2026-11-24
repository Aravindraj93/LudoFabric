import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from gameagent.docs import list_docs


class DocumentTests(unittest.TestCase):
    def test_hashed_brief_filename_uses_task_title(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompts = root / "prompts"
            prompts.mkdir()
            key = hashlib.sha256(b"coin_collection").hexdigest()
            (prompts / f"{key}.md").write_text("Build coins")
            docs = list_docs(root, {"plan": {"tasks": [
                {"id": "coin_collection", "title": "Coin collection"}
            ]}})
            brief = next(d for d in docs if d["id"].startswith("task-"))
            self.assertEqual(brief["title"], "Task brief: Coin collection")
            self.assertEqual(brief["content"], "Build coins")


if __name__ == "__main__":
    unittest.main()

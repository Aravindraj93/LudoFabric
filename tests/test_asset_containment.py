import io
from pathlib import Path
from tempfile import TemporaryDirectory
import stat
import unittest
from unittest.mock import Mock
import zipfile

from gameagent.assets.safety import Budget, contained, download, extract, url_name
from gameagent.assets.manager import AssetManager
from gameagent.config import Config
from gameagent.schemas import AssetRequest


class AssetContainmentTests(unittest.TestCase):
    def test_keys_reject_traversal_devices_and_absolute_paths(self):
        for key in ("../outside", "C:/outside", "con", "nul", "a:b", "Upper", "x" * 65):
            with self.subTest(key=key), self.assertRaises(ValueError):
                AssetRequest(key=key, query="tree")
        self.assertEqual(AssetRequest(key="hero_tree", query="tree").key, "hero_tree")

    def test_path_rules_and_url_names(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in ("../x", "/x", "C:/x", "x:stream", "a/../x", "con.glb", "x.", "x/", "a//b"):
                with self.subTest(rel=rel), self.assertRaises(ValueError):
                    contained(root, rel)
            self.assertEqual(contained(root, "textures/tree.png"), root / "textures/tree.png")
        self.assertEqual(url_name("https://example.com/model.glb?token=x"), "model.glb")
        with self.assertRaises(ValueError):
            url_name("https://example.com/%2fescape")

    def test_stream_limit_preserves_existing_file_and_removes_partial(self):
        with TemporaryDirectory() as tmp:
            dest = Path(tmp) / "model.glb"
            dest.write_bytes(b"original")
            response = Mock(headers={})
            response.iter_bytes.return_value = iter([b"123", b"456"])
            context = Mock()
            context.__enter__ = Mock(return_value=response)
            context.__exit__ = Mock(return_value=False)
            client = Mock(stream=Mock(return_value=context))
            with self.assertRaises(ValueError):
                download(client, "https://example.com/a", dest, Budget(max_file=4))
            self.assertEqual(dest.read_bytes(), b"original")
            self.assertEqual(list(dest.parent.glob(".download-*")), [])

    def test_archive_rejects_unsafe_and_duplicate_paths(self):
        for names in (("../escape",), ("Model.gltf", "model.gltf")):
            with TemporaryDirectory() as tmp:
                archive = Path(tmp) / "a.zip"
                with zipfile.ZipFile(archive, "w") as z:
                    for name in names:
                        z.writestr(name, "data")
                with self.assertRaises(ValueError):
                    extract(archive, Path(tmp) / "out", Budget())
                self.assertFalse((Path(tmp) / "out").exists())

    def test_archive_links_and_expanded_limits(self):
        with TemporaryDirectory() as tmp:
            archive = Path(tmp) / "a.zip"
            link = zipfile.ZipInfo("link")
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr(link, "outside")
            with self.assertRaises(ValueError):
                extract(archive, Path(tmp) / "out", Budget())
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("model.gltf", b"12345")
            with self.assertRaises(ValueError):
                extract(archive, Path(tmp) / "out", Budget(max_file=4))
            extract(archive, Path(tmp) / "out", Budget())
            self.assertEqual((Path(tmp) / "out/model.gltf").read_bytes(), b"12345")

    def test_failed_staging_never_deletes_existing_library(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = AssetManager(Config(), root, root)
            try:
                dest = root / "Assets/ThirdParty/PolyHaven/Model/tree"
                dest.mkdir(parents=True)
                (dest / "user.txt").write_text("keep")
                def fail(stage):
                    (stage / "partial").write_text("partial")
                    raise ValueError("failed")
                with self.assertRaises(ValueError):
                    manager._acquire(dest, fail)
                self.assertEqual((dest / "user.txt").read_text(), "keep")
                self.assertFalse(list(dest.parent.glob(".asset-staging-*")))
            finally:
                manager.http.close()


if __name__ == "__main__":
    unittest.main()

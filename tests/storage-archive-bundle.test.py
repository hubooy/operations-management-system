import importlib.util
import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("bundle", Path(__file__).resolve().parents[1] / "tools/storage-archive-bundle.py")
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


class ArchiveTest(unittest.TestCase):
    def test_linked_parent_cannot_redirect_extraction(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / "outside").mkdir()
            try:
                os.symlink(root / "outside", root / "linked", target_is_directory=True)
            except OSError:
                self.skipTest("Creating symbolic links is unavailable")
            with tarfile.open(root / "empty.tar.gz", "w:gz"):
                pass
            with self.assertRaises(ValueError):
                bundle.unpack(root / "empty.tar.gz", root / "linked/output")
            self.assertFalse((root / "outside/output").exists())

    def test_round_trip_includes_dot_files_unicode_and_empty_directories(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "source"
            source.mkdir()
            (source / "empty").mkdir()
            (source / ".bin").mkdir()
            (source / ".bin/tool.cmd").write_bytes(b"synthetic tool")
            (source / "中文.json").write_bytes(b"{}")
            bundle.pack(source, root / "archive.tar.gz")
            bundle.unpack(root / "archive.tar.gz", root / "restored")
            self.assertEqual((root / "restored/.bin/tool.cmd").read_bytes(), b"synthetic tool")
            self.assertEqual((root / "restored/中文.json").read_bytes(), b"{}")
            self.assertTrue((root / "restored/empty").is_dir())
            with self.assertRaises(FileExistsError):
                bundle.unpack(root / "archive.tar.gz", root / "restored")

    def test_malicious_members_fail_without_escape(self):
        for name in ["../escape", "/absolute", "C:/escape", "a\\..\\escape", "file:stream", "NUL", "a.", "a "]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as root:
                root = Path(root)
                with tarfile.open(root / "bad.tar.gz", "w:gz") as archive:
                    member = tarfile.TarInfo(name)
                    member.size = 1
                    archive.addfile(member, io.BytesIO(b"x"))
                with self.assertRaises(ValueError):
                    bundle.unpack(root / "bad.tar.gz", root / "output")
                self.assertFalse((root / "escape").exists())

    def test_links_and_case_aliases_are_rejected(self):
        for type_ in [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE]:
            with tempfile.TemporaryDirectory() as root:
                root = Path(root)
                with tarfile.open(root / "bad.tar.gz", "w:gz") as archive:
                    member = tarfile.TarInfo("link")
                    member.type = type_
                    member.linkname = "../escape"
                    archive.addfile(member)
                with self.assertRaises(ValueError):
                    bundle.unpack(root / "bad.tar.gz", root / "output")
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            with tarfile.open(root / "bad.tar.gz", "w:gz") as archive:
                for name in ["same", "SAME"]:
                    archive.addfile(tarfile.TarInfo(name), io.BytesIO())
            with self.assertRaises(ValueError):
                bundle.unpack(root / "bad.tar.gz", root / "output")


if __name__ == "__main__":
    unittest.main()

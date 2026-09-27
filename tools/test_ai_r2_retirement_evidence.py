import importlib.util
from pathlib import Path
import sqlite3
from contextlib import closing
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("ai_r2_evidence", Path(__file__).with_name("ai-r2-retirement-evidence.py"))
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


class RetirementEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.database = self.root / "bucket.sqlite"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.executescript("""
                CREATE TABLE _mf_objects(key TEXT,size INTEGER,etag TEXT,version TEXT,blob_id TEXT);
                CREATE TABLE _mf_multipart_uploads(key TEXT);
                CREATE TABLE _mf_multipart_parts(object_key TEXT);
                INSERT INTO _mf_objects VALUES('market/image',4,'etag','version','blob');
                INSERT INTO _mf_objects VALUES('ai-space-other/image',2,'etag','version','blob2');
            """)

    def test_empty_ai_preserves_other_prefixes_and_database_bytes(self):
        before = self.database.read_bytes()
        result = evidence.collect(self.root)
        self.assertEqual(result["objectCount"], 0)
        self.assertEqual(result["preservedObjectCount"], 2)
        self.assertEqual(self.database.read_bytes(), before)

    def test_any_ai_object_upload_or_part_prevents_retirement(self):
        for table, columns, values in (
            ("_mf_objects", "key,size,etag,version,blob_id", "'ai-space/v1/job/image.png',1,'e','v','b'"),
            ("_mf_multipart_uploads", "key", "'ai-space/v1/job/image.png'"),
            ("_mf_multipart_parts", "object_key", "'ai-space/v1/job/image.png'"),
        ):
            with self.subTest(table=table):
                with closing(sqlite3.connect(self.database)) as db, db:
                    db.execute(f"INSERT INTO {table}({columns}) VALUES({values})")
                before = self.database.read_bytes()
                with self.assertRaises(ValueError):
                    evidence.collect(self.root)
                self.assertEqual(self.database.read_bytes(), before)
                with closing(sqlite3.connect(self.database)) as db, db:
                    db.execute(f"DELETE FROM {table} WHERE substr({columns.split(',')[0]},1,9)='ai-space/'")

    def test_ambiguous_bucket_refused(self):
        (self.root / "second.sqlite").write_bytes(self.database.read_bytes())
        with self.assertRaises(ValueError):
            evidence.collect(self.root)


if __name__ == "__main__":
    unittest.main()

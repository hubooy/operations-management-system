"""Synthetic evidence compatibility only; does not connect to PostgreSQL."""
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("backup_fixtures",ROOT/"tests/postgres-consistent-backup.test.py")
fixtures=importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
TABLES={"sales_analysis_options","sales_analysis_options_state"}
OLD=[("sales","0009_postgres_raw_upload_payload")]
NEW=[*OLD,("sales","0010_analysis_options")]


class SalesOptionsBackupTests(unittest.TestCase):
    def test_old_backup_remains_valid_without_projection(self):
        evidence=fixtures._ai_evidence(set(),OLD)
        self.assertFalse(set(evidence["tables"]) & TABLES)

    def test_current_requires_both_exact_tables_and_predecessor(self):
        evidence=fixtures._ai_evidence(TABLES,NEW)
        self.assertTrue(TABLES <= set(evidence["tables"]))
        for missing in TABLES:
            with self.assertRaises(RuntimeError): fixtures._ai_evidence(TABLES-{missing},NEW)
        with self.assertRaises(RuntimeError):
            fixtures._ai_evidence(TABLES,[("sales","0010_analysis_options")])

    def test_unrecorded_new_tables_cannot_masquerade_as_legacy(self):
        for present in (TABLES,{"sales_analysis_options"},{"sales_analysis_options_state"}):
            with self.assertRaises(RuntimeError): fixtures._ai_evidence(present,OLD)


if __name__=="__main__": unittest.main()

"""No-DB 0071 contract: creation-only sidecar, no report authority."""
from importlib import import_module
from pathlib import Path
from unittest import TestCase

from . import business_v4_report_link_sql as link
from .table_manifest import (AI_TABLES, AI_SQL_ONLY_0070,
    AI_SQL_ONLY_0071, AI_FULL_TABLES_PRE_V4_REPORT_LINKS,
    AI_FULL_TABLES_AFTER_V4_REPORT_LINKS)


class V4ReportLinkContractTests(TestCase):
    def test_exact_history_and_new_table_inventory(self):
        migration = import_module(
            "ai_assistant.migrations.0071_business_v4_report_source_link")
        self.assertEqual(migration.Migration.dependencies,
            [("ai_assistant",
                "0070_business_promotion_budget_v11_limited_identity")])
        from .database_contract import MODELS
        self.assertEqual(set(AI_TABLES), set(MODELS))
        self.assertEqual(len(AI_TABLES), 89)
        self.assertEqual(len(AI_SQL_ONLY_0070), 2)
        self.assertEqual(len(AI_SQL_ONLY_0071), 2)
        self.assertTrue(all(item.startswith("protected_business_v4_report_")
            and not item.startswith("ai_") for item in AI_SQL_ONLY_0071))
        self.assertEqual(len(AI_FULL_TABLES_PRE_V4_REPORT_LINKS), 91)
        self.assertEqual(len(AI_FULL_TABLES_AFTER_V4_REPORT_LINKS), 93)
        self.assertEqual(set(AI_FULL_TABLES_AFTER_V4_REPORT_LINKS)-
            set(AI_FULL_TABLES_PRE_V4_REPORT_LINKS),
            {"protected_business_v4_report_link_intents", "protected_business_v4_report_source_links"})

    def test_consistent_backup_archives_all_tables_while_ai_gate_stays_exact(self):
        backup = (Path(__file__).resolve().parents[2] / "tools" /
            "postgres-consistent-backup.py").read_text(encoding="utf-8")
        self.assertIn('name.startswith("ai_")', backup)
        self.assertIn('"--format=custom"', backup)
        self.assertNotIn('"--table=', backup)
        self.assertNotIn('"--exclude-table', backup)
        health = (Path(__file__).resolve().parent / "health.py").read_text(
            encoding="utf-8")
        self.assertIn("0071_business_v4_report_source_link\").verify_catalog(cursor)",
            health)

    def test_create_transaction_and_read_only_do_not_grant_authority(self):
        self.assertIn("EXISTS (SELECT 1 FROM public.ai_report_runs",
            link.ISSUE_SQL)
        self.assertIn("intent.issued_txid IS DISTINCT FROM txid_current()",
            link.REPORT_TRIGGER)
        self.assertIn("AFTER INSERT", import_module(
            "ai_assistant.migrations.0071_business_v4_report_source_link")
            .install.__code__.co_consts.__str__())
        self.assertIn("'creationTimeLinkPersisted',true", link.READ_SQL)
        for field in ("appHmacVerified", "authorityVerified",
                "reportGenerationSupported", "agentDispatchSupported",
                "rendererRegistered", "downloadSupported"):
            self.assertIn("'"+field+"',false", link.READ_SQL)
        self.assertNotIn("'authorityVerified',true", link.READ_SQL)
        self.assertIn("session_user IS DISTINCT FROM 'teruisi_ai_writer'",
            link.ISSUE_SQL)
        self.assertIn("session_user IS DISTINCT FROM 'teruisi_ai_writer'",
            link.CREATE_REPORT_SQL)
        self.assertIn("intent.issued_txid IS DISTINCT FROM txid_current()",
            link.CREATE_REPORT_SQL)
        self.assertEqual(link.CREATE_REPORT_SQL.count(
            "WHERE id=1 FOR SHARE;"), 2)
        self.assertEqual(link.CREATE_REPORT_SQL.count(
            "ai_v4_report_link_create_authority_invalid"), 2)
        self.assertLess(link.CREATE_REPORT_SQL.index(
            "SELECT * INTO authority FROM public.ai_write_authority"),
            link.CREATE_REPORT_SQL.index("SELECT * INTO intent FROM "))
        self.assertLess(link.CREATE_REPORT_SQL.rindex(
            "SELECT * INTO authority FROM public.ai_write_authority"),
            link.CREATE_REPORT_SQL.index("INSERT INTO public.ai_report_runs"))
        self.assertIn("workflow_id,budget_plan_id,snapshot_json,created_at)",
            link.CREATE_REPORT_SQL)
        self.assertIn("flow.id,NULL,snapshot_text,clock_timestamp()",
            link.CREATE_REPORT_SQL)
        self.assertIn("session_user IS DISTINCT FROM 'teruisi_ai_reader'",
            link.READ_SQL)
        self.assertIn("IF TG_OP IS DISTINCT FROM 'INSERT'", link.ROW_GUARD)
        self.assertNotIn("sha256(convert_to(parent.plan_json", link.BINDINGS_SQL)
        self.assertNotIn("sha256(convert_to(seal.body_json", link.BINDINGS_SQL)

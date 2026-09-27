"""Pure 0077 freeze/default-close checks before isolated PostgreSQL."""
from hashlib import sha256
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PausedTopology0077StaticTests(unittest.TestCase):
    def test_frozen_market_predecessor_files_unchanged(self):
        expected = {
            "0060_business_market_v2_execution_snapshot.py":
                "2884d0e7c0996817cdb3e3c5858775a17307a5f6b08f6c6b5b5182dea9fb71b7",
            "0062_business_market_v2_read_receipt_candidate.py":
                "0a690fd3f05ae8735af70652797670a069fd4eeb484dd14757b6cbfe07ed4b70",
            "0064_business_market_v2_synthetic_vertical.py":
                "732352f424c4750162bc6e48e93962f8d8a91981e6d6286f10215d86343c1740",
            "0074_business_market_v2_human_cap_approval.py":
                "419091ebdbc0766e4e3d8defd8749a176484d2a988fb05e0877bf0ba42183852",
            "0076_business_promotion_budget_v11_ticket_bound_signer.py":
                "08632281d91e88803d6daab0b7b90a3eea7261019ac0493423287663e97be737",
        }
        for name, wanted in expected.items():
            with self.subTest(name=name):
                actual = sha256((ROOT / "backend/ai_assistant/migrations" /
                    name).read_bytes()).hexdigest()
                self.assertEqual(actual, wanted)

    def test_formal_paths_reject_0077(self):
        migration = "0077_business_market_v6_paused_topology"
        table = "protected_business_market_v6_topology_cancellations"
        files = {
            name: (ROOT / name).read_text(encoding="utf-8")
            for name in ("tools/django-local-service.ps1",
                "tools/django-postgres-maintenance.ps1",
                "tools/postgres-consistent-backup.py",
                "tools/protected-ai-restore-static-audit.py")
        }
        self.assertIn(migration + ".py", files["tools/django-local-service.ps1"])
        self.assertIn("|76|77", files["tools/django-postgres-maintenance.ps1"])
        self.assertIn(migration, files["tools/postgres-consistent-backup.py"])
        self.assertIn(table, files["tools/postgres-consistent-backup.py"])
        self.assertIn(migration, files["tools/protected-ai-restore-static-audit.py"])

    def test_no_paid_or_direct_table_authority_added(self):
        migration = (ROOT / "backend/ai_assistant/migrations/"
            "0077_business_market_v6_paused_topology.py").read_text(
            encoding="utf-8")
        sql = (ROOT / "backend/ai_assistant/"
            "business_market_v6_paused_topology_sql.py").read_text(
            encoding="utf-8")
        self.assertIn("NOLOGIN NOINHERIT", migration)
        self.assertIn("def verify_catalog(", migration)
        self.assertNotIn("GRANT SELECT ON public.ai_agent", migration)
        self.assertNotIn("GRANT INSERT ON public.ai_agent", migration)
        self.assertIn("'[]',''", sql)
        self.assertIn("'paused','paused'", sql)
        self.assertIn("ai_market_v6_topology_effect_closed", sql)
        self.assertEqual(sql.count("'127.0.0.1/32','::1','::1/128'"),3)
        self.assertNotIn("provider.turn", sql)

    def test_cancel_fk_is_pinned_to_topology_report_primary_key(self):
        migration = (ROOT / "backend/ai_assistant/migrations/"
            "0077_business_market_v6_paused_topology.py").read_text(
            encoding="utf-8")
        sql = (ROOT / "backend/ai_assistant/"
            "business_market_v6_paused_topology_sql.py").read_text(
            encoding="utf-8")
        self.assertIn("protected_business_market_v6_topologies(report_id)",sql)
        self.assertIn('"report_id" if table == v6.CANCEL_TABLE else "id"',
            migration)

    def test_empty_reverse_keeps_closed_cluster_role_for_second_restore(self):
        migration = (ROOT / "backend/ai_assistant/migrations/"
            "0077_business_market_v6_paused_topology.py").read_text(
            encoding="utf-8")
        self.assertNotIn('cursor.execute("DROP ROLE " + v6.ROLE)',migration)
        self.assertIn("if cursor.fetchone() == (None,):",migration)
        self.assertIn("_role(cursor, closed=True)",migration)
        self.assertIn("topology role gained direct table ACL",migration)
        self.assertNotIn("LIKE 'ai_%'",migration)
        self.assertIn('["ai_%","protected_business_%",v6.ROLE]',migration)

    def test_current_graph_bytes_fit_the_same_32k_sql_and_db_cap(self):
        import sys
        sys.path.insert(0,str(ROOT / "backend"))
        from ai_assistant.business_market_v2_execution_snapshot_contract import graph
        from business_analysis.contracts import canonical
        self.assertEqual([len(canonical(graph(flag)).encode("utf-8"))
            for flag in (False,True)], [17321,18040])
        sql = (ROOT / "backend/ai_assistant/"
            "business_market_v6_paused_topology_sql.py").read_text(
            encoding="utf-8")
        self.assertIn("octet_length(graph_text) NOT BETWEEN 1 AND 32768",sql)
        self.assertIn("octet_length(graph_json) <= 32768",sql)

    def test_0065_inner_candidate_and_row_bytes_are_distinct_authorities(self):
        import sys
        sys.path.insert(0,str(ROOT / "backend"))
        from ai_assistant.test_business_market_v2_cost_candidate import build
        from business_analysis.contracts import canonical, digest
        value = build()
        inner = value["candidateDigest"]
        raw = canonical(value)
        outer = sha256(raw.encode("utf-8")).hexdigest()
        self.assertEqual(inner,digest({key:item for key,item in value.items()
            if key!="candidateDigest"}))
        self.assertNotEqual(inner,outer)
        self.assertNotEqual(sha256((raw+" ").encode("utf-8")).hexdigest(),outer)
        self.assertNotEqual("0"*64,inner)
        sql = (ROOT / "backend/ai_assistant/"
            "business_market_v6_paused_topology_sql.py").read_text(
            encoding="utf-8")
        self.assertIn("cost_row.candidate_digest IS DISTINCT FROM encode(sha256(",sql)
        self.assertIn("cost_json:=public.ai_market_v2_cost_candidate_expected(",sql)
        self.assertIn("cost_json->>'candidateDigest' IS DISTINCT FROM",sql)

    def test_job_key_json_operator_precedence_is_explicit(self):
        sql = (ROOT / "backend/ai_assistant/"
            "business_market_v6_paused_topology_sql.py").read_text(
            encoding="utf-8")
        self.assertNotIn("||item->>'key'",sql)
        self.assertIn("||(item->>'key')",sql)


if __name__ == "__main__":
    unittest.main()

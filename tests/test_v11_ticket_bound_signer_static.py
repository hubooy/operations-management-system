"""No PostgreSQL: frozen predecessors and formal default-closed sources."""
import hashlib
from pathlib import Path
import sys
import unittest


ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"backend"))
from ai_assistant import business_promotion_budget_v11_ticket_sign_sql as v3


class V11TicketBoundSignerStaticTests(unittest.TestCase):
    def test_predecessor_migration_bytes_are_frozen(self):
        expected={
            "0068_business_promotion_budget_v11_verifier_receipt.py":
                "c299237d9c2534f9a9097a5cf61875a360af526e6c765fce86a9f0c27cee628b",
            "0070_business_promotion_budget_v11_limited_identity.py":
                "9931668c379ab94d5ebedff1873a03117952c72e687fd0a8816871ed6570507f",
            "0073_business_promotion_budget_v11_login_attestation.py":
                "7d6d1e457e4ab05c3afdc111ceb6795a7735f27c0fc798bde9027bbf97fe35b3"}
        for name,digest in expected.items():
            with self.subTest(name=name):
                actual=hashlib.sha256((ROOT/"backend/ai_assistant/migrations"/name
                    ).read_bytes()).hexdigest()
                self.assertEqual(actual,digest)

    def test_v3_mac_is_new_purpose_without_old_sql_mutation(self):
        source=v3.private_mac_sql()
        self.assertIn("private_mac_valid_v3",source)
        self.assertIn(v3.DOMAIN,source)
        self.assertNotIn("teruisi:budget-v11:protected-verifier:v1",source)
        self.assertIn("claimId",v3.RECORD_SQL)
        self.assertIn("ticketId",v3.RECORD_SQL)
        self.assertIn("filePageRoot",v3.RECORD_SQL)
        self.assertIn("ledgerRoot",v3.RECORD_SQL)
        self.assertNotIn("SET status='ready'",v3.RECORD_SQL)

    def test_record_rechecks_current_report_flow_and_actor_under_lock(self):
        source=v3.REQUIREMENTS_SQL
        for marker in ("WHERE id=parent.report_id FOR SHARE",
                "WHERE id=report.workflow_id FOR SHARE",
                "report.owner_email IS DISTINCT FROM parent.owner_email",
                "flow.owner_email IS DISTINCT FROM parent.owner_email",
                "report.scope_json IS DISTINCT FROM parent.scope_json",
                "flow.scope_json IS DISTINCT FROM parent.scope_json",
                "flow.status<>'completed'",
                "assertion->>'reportSnapshotSha256'",
                "assertion->>'workflowInputSha256'",
                "actor.version::text IS DISTINCT FROM assertion->>'actorVersion'",
                "WHERE email=parent.owner_email FOR SHARE",
                "WHERE id=report.budget_plan_id FOR SHARE",
                "'workflowVersion',flow.version"):
            self.assertIn(marker,source)
        self.assertIn("value->>'workflowVersion' IS DISTINCT FROM "
            "req->>'workflowVersion'",v3.RECORD_SQL)

    def test_formal_migration_backup_restore_guards_know_0076(self):
        migration="0076_business_promotion_budget_v11_ticket_bound_signer"
        table="protected_business_budget_v11_signed_receipts_v3"
        backup=(ROOT/"tools/postgres-consistent-backup.py").read_text(
            encoding="utf-8")
        operator=(ROOT/"tools/django-postgres-maintenance.ps1").read_text(
            encoding="utf-8")
        service=(ROOT/"tools/django-local-service.ps1").read_text(
            encoding="utf-8")
        self.assertIn(migration,backup)
        self.assertIn(table,backup)
        self.assertIn("|75|76",operator)
        self.assertIn(migration+".py",service)
        self.assertIn("protected AI daily backup is not admitted",backup)
        self.assertIn("protected AI archive restore is not admitted",backup)

    def test_four_private_ledgers_have_no_direct_reader_grant(self):
        migration=(ROOT/"backend/ai_assistant/migrations/"
            "0076_business_promotion_budget_v11_ticket_bound_signer.py"
            ).read_text(encoding="utf-8")
        self.assertIn("direct protected read was widened",migration)
        self.assertNotIn("GRANT SELECT ON public.ai_agent_provider_dispatches",
            migration)
        self.assertNotIn("GRANT SELECT ON public.ai_agent_tool_results",migration)
        self.assertIn("def verify_catalog(",migration)


if __name__=="__main__":
    unittest.main()

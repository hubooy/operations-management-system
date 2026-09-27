"""Isolated PostgreSQL tests for the internal finance owning source."""
from unittest.mock import patch
from uuid import uuid4

from django.db import connection, transaction, DatabaseError
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from access_control.models import AccessRole, AppUser
from business_analysis.contracts import digest
from finance import business_analysis_source as service
from finance.business_source_permissions import grant_actor_read
from finance.errors import FinanceApiError
from finance.import_service import import_finance_payload
from finance.models import FinanceWriteAuthority, FinanceDataRevision, FinanceMonth, FinanceLine
from finance.tests.factories import prepared_payload
from sales.auth import Principal


class FinanceBusinessSourceTests(TestCase):
    def setUp(self):
        FinanceWriteAuthority.objects.filter(id=1).update(status="postgres")
        self.principal = Principal("finance-source@example.test", "Synthetic", "admin", None)
        role, _ = AccessRole.objects.get_or_create(code="admin", defaults={"rank": 40, "label": "Admin"})
        now = timezone.now()
        AppUser.objects.create(email=self.principal.email, role=role, status="active", scope=None,
            display_name="Synthetic", version=1, created_at=now, updated_at=now)
        import_finance_payload(prepared_payload("2026-08"), self.principal.email)
        self.query = {"months": ["2026-08"], "scope": {"scope_key": "business", "scope_type": "business",
            "scope_name": "志高事业部", "group_name": ""}}

    def test_actual_import_multiple_pages_nulls_and_read_only(self):
        FinanceLine.objects.filter(month="2026-08", scope_key="business", metric_key="net_sales").update(amount_cents=None)
        with patch.object(service, "READ_ROWS", 2), CaptureQueriesContext(connection) as captured:
            with service.open_source(self.principal, self.query) as (source, binding):
                self.assertEqual(source.manifest["coverage"][0]["metrics"]["net_sales"]["status"], "missing_value")
                self.assertGreater(source.manifest["rowCount"], 2)
                self.assertFalse(binding["persistentEvidenceVerified"])
                self.assertEqual(binding["sourceDigest"], source.manifest["sourceDigest"])
                binding["actor"]["version"] = 99  # returned DTO cannot alter fences
        self.assertTrue(all(q["sql"].lstrip().upper().startswith("SELECT") for q in captured))

    def test_missing_month_is_gap_but_incomplete_or_missing_batch_rejects(self):
        self.query["months"].append("2026-09")
        with service.open_source(self.principal, self.query) as (source, _):
            self.assertEqual(source.manifest["coverage"][1]["metrics"]["net_sales"]["status"], "missing_month")
        for change in ({"status": "processing"}, {"status": "completed", "batch_id": "missing"}):
            FinanceMonth.objects.filter(month="2026-08").update(**change)
            with self.assertRaises(FinanceApiError):
                with service.open_source(self.principal, self.query): pass

    def test_absent_disabled_scoped_actor_no_fallback(self):
        for change in ({"status": "disabled"}, {"status": "active", "scope": {} }):
            AppUser.objects.filter(email=self.principal.email).update(**change)
            with self.assertRaises(FinanceApiError):
                with service.open_source(self.principal, self.query): pass
        AppUser.objects.filter(email=self.principal.email).delete()
        with self.assertRaises(FinanceApiError):
            with service.open_source(self.principal, self.query): pass

    def test_late_actor_change_blocks_normal_context_exit(self):
        with self.assertRaises(FinanceApiError):
            with service.open_source(self.principal, self.query):
                AppUser.objects.filter(email=self.principal.email).update(version=2)

    def test_full_digest_change_with_same_prefix_blocks_exit(self):
        with self.assertRaises(FinanceApiError):
            with service.open_source(self.principal, self.query):
                rev = FinanceDataRevision.objects.get(domain="finance")
                tail = "1" if rev.source_digest[-1] != "1" else "2"
                FinanceDataRevision.objects.filter(domain="finance").update(
                    revision=rev.revision + 1,
                    source_digest=rev.source_digest[:-1] + tail)

    def test_revision_change_between_keyset_pages_returns_no_source(self):
        original = service._revalidate
        calls = []
        def changing(*args):
            calls.append(1)
            if len(calls) == 3:
                revision = FinanceDataRevision.objects.get(domain="finance")
                FinanceDataRevision.objects.filter(domain="finance").update(
                    revision=revision.revision + 1,
                    source_digest=digest([revision.source_digest, "page-change"]))
            return original(*args)
        with patch.object(service, "READ_ROWS", 2), patch.object(service, "_revalidate", side_effect=changing):
            with self.assertRaises(FinanceApiError):
                with service.open_source(self.principal, self.query): self.fail("partial source escaped")
        self.assertEqual(len(calls), 3)

    def test_publication_change_without_revision_blocks_exit(self):
        with self.assertRaises(FinanceApiError) as caught:
            with service.open_source(self.principal, self.query):
                FinanceMonth.objects.filter(month="2026-08").update(status="processing")
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.code, "analysis_revision_changed")

    def test_revocation_during_exit_metadata_read_is_rechecked(self):
        original = service._metadata
        def revoking(query):
            result = original(query)
            AppUser.objects.filter(email=self.principal.email).update(status="disabled")
            return result
        with self.assertRaises(FinanceApiError) as caught:
            with service.open_source(self.principal, self.query):
                # Patch only after preparation; revoke during final metadata SQL.
                replacement = patch.object(service, "_metadata", side_effect=revoking)
                replacement.start()
                self.addCleanup(replacement.stop)
        self.assertEqual(caught.exception.status, 403)

    def test_caller_exception_is_preserved_without_exit_revalidation(self):
        error = RuntimeError("caller cancelled")
        with self.assertRaises(RuntimeError) as caught:
            with service.open_source(self.principal, self.query):
                FinanceMonth.objects.filter(month="2026-08").update(status="processing")
                raise error
        self.assertIs(caught.exception, error)

    def test_wrong_exact_group_does_not_alias_existing_scope(self):
        self.query["scope"]["group_name"] = "其他组"
        with service.open_source(self.principal, self.query) as (source, _):
            self.assertEqual(source.manifest["rowCount"], 0)
            self.assertFalse(source.manifest["allCoreMetricsPresent"])

    def test_minimum_actor_columns_role_and_no_write_or_display_name(self):
        if connection.vendor != "postgresql": self.skipTest("PostgreSQL grants")
        role = "finance_source_test_" + uuid4().hex[:12]
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE ROLE "{role}" NOLOGIN')
            cursor.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
            grant_actor_read(cursor, role)
            cursor.execute(f'GRANT SELECT ON finance_lines, finance_months, finance_import_batches, finance_data_revisions TO "{role}"')
            cursor.execute(f'SET LOCAL ROLE "{role}"')
        try:
            with service.open_source(self.principal, self.query) as (source, _):
                self.assertGreater(source.manifest["rowCount"], 0)
            for statement in ("SELECT display_name FROM access_control_users", "UPDATE access_control_users SET version=version",
                              "UPDATE finance_lines SET amount_cents=amount_cents"):
                with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(statement)
        finally:
            with connection.cursor() as cursor: cursor.execute("RESET ROLE")

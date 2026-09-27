"""Real PostgreSQL transaction tests for finance fact/revision coupling."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4
from unittest.mock import patch

from django.db import DatabaseError, close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from access_control.models import AccessRole, AppUser
from business_analysis.contracts import digest
from finance.business_evidence_page import read_page
from finance.errors import FinanceApiError
from finance.import_service import import_finance_payload
from finance.models import (FinanceDataRevision, FinanceImportBatch, FinanceLine,
    FinanceMonth, FinanceTarget, FinanceWriteAuthority)
from .factories import changed_raw_file, prepared_payload
from sales.auth import Principal


class FinanceSourceRevisionGuardTests(TransactionTestCase):
    reset_sequences = False

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires PostgreSQL deferred triggers and roles")
        FinanceWriteAuthority.objects.update_or_create(id=1,
            defaults={"status": "postgres"})
        FinanceDataRevision.objects.get_or_create(domain="finance",
            defaults={"revision": 0, "source_digest": "0" * 64})

    def revision(self):
        return FinanceDataRevision.objects.get(domain="finance")

    def bump(self, label):
        item = FinanceDataRevision.objects.select_for_update().get(domain="finance")
        item.revision += 1
        item.source_digest = digest([item.source_digest, label, item.revision])
        item.save(update_fields=["revision", "source_digest"])

    def create_line(self, subject="synthetic"):
        return FinanceLine.objects.create(month="2026-08", section="summary",
            metric_key="net_sales", subject_name=subject, scope_key="business",
            scope_type="business", scope_name="Synthetic", group_name="",
            value_type="amount", amount_cents=100, rate_bps=None,
            raw_value="1.00", source_row_count=1, sort_order=1,
            is_total=False, created_at="synthetic")

    def seed_line(self, subject="synthetic"):
        with transaction.atomic():
            row = self.create_line(subject)
            self.bump("seed-" + subject)
        return row

    def marker_count(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM public.finance_source_revision_markers")
            return cursor.fetchone()[0]

    def test_initial_missing_revision_row_seeds_only_with_valid_fact_commit(self):
        # Only the isolated migration owner can simulate a pre-0004 absent row.
        # The production finance_writer cannot disable this trigger or DELETE it.
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE finance_data_revisions "
                "DISABLE TRIGGER finance_revision_monotonic")
        try:
            FinanceDataRevision.objects.filter(domain="finance").delete()
        finally:
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE finance_data_revisions "
                    "ENABLE TRIGGER finance_revision_monotonic")
        with self.assertRaises(DatabaseError), transaction.atomic():
            self.create_line("missing-revision")
        self.assertFalse(FinanceDataRevision.objects.filter(domain="finance").exists())
        self.assertFalse(FinanceLine.objects.filter(subject_name="missing-revision").exists())
        self.assertEqual(self.marker_count(), 0)
        first = import_finance_payload(prepared_payload("2026-08"),
            "synthetic@example.invalid")
        self.assertEqual(first["status"], "imported")
        self.assertEqual(self.revision().revision, 1)
        self.assertEqual(self.revision().source_digest,
            FinanceImportBatch.objects.get(id=first["batch"]["id"]).published_state_token)
        self.assertEqual(self.marker_count(), 0)

    def test_unversioned_line_month_and_batch_writes_roll_back_at_commit(self):
        line = self.seed_line()
        baseline = (self.revision().revision, self.revision().source_digest)
        operations = (
            lambda: FinanceLine.objects.filter(pk=line.pk).update(amount_cents=200),
            lambda: FinanceLine.objects.filter(pk=line.pk).delete(),
            lambda: FinanceLine.objects.create(month="2026-08", section="summary",
                metric_key="profit", subject_name="new", scope_key="business",
                scope_type="business", scope_name="Synthetic", group_name="",
                value_type="amount", amount_cents=7, raw_value="0.07",
                source_row_count=1, sort_order=2, is_total=False,
                created_at="synthetic"),
            lambda: FinanceMonth.objects.create(month="2026-08", batch_id="synthetic",
                sheet_name="s", business_name="b", source_file_name="f",
                status="completed", imported_at="synthetic"),
            lambda: FinanceImportBatch.objects.create(id="synthetic", source="test",
                file_name="synthetic.xlsx", file_size_bytes=1,
                file_hash="a" * 64, status="completed", created_at="synthetic"),
        )
        for change in operations:
            with self.subTest(operation=str(change)), self.assertRaises(DatabaseError):
                with transaction.atomic():
                    change()
            self.assertEqual((self.revision().revision, self.revision().source_digest), baseline)
            self.assertEqual(self.marker_count(), 0)
        line.refresh_from_db()
        self.assertEqual(line.amount_cents, 100)
        self.assertEqual(FinanceLine.objects.count(), 1)
        self.assertFalse(FinanceMonth.objects.exists())
        self.assertFalse(FinanceImportBatch.objects.exists())

    def test_normal_import_keeps_single_revision_step_duplicate_and_failure(self):
        baseline = self.revision().revision
        payload = prepared_payload("2026-08")
        first = import_finance_payload(payload, "synthetic@example.invalid")
        self.assertEqual(first["status"], "imported")
        after = self.revision()
        self.assertEqual(after.revision, baseline + 1)
        self.assertEqual(after.source_digest,
            FinanceImportBatch.objects.get(id=first["batch"]["id"]).published_state_token)
        self.assertEqual(self.marker_count(), 0)
        duplicate = import_finance_payload(changed_raw_file(payload),
            "synthetic@example.invalid")
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(self.revision().revision, after.revision)

        replacement = deepcopy(payload)
        replacement["rawFileHash"] = "f" * 64
        replacement["months"][0]["lines"][0]["amountCents"] += 1
        before_rows = list(FinanceLine.objects.values_list("id", "amount_cents"))
        with patch.object(FinanceLine.objects, "bulk_create", side_effect=RuntimeError("rollback")):
            with self.assertRaises(RuntimeError):
                import_finance_payload(replacement, "synthetic@example.invalid")
        self.assertEqual(list(FinanceLine.objects.values_list("id", "amount_cents")), before_rows)
        self.assertEqual(self.revision().revision, after.revision)
        self.assertEqual(self.marker_count(), 0)

    def test_target_only_does_not_require_fact_revision(self):
        baseline = (self.revision().revision, self.revision().source_digest)
        with transaction.atomic():
            FinanceTarget.objects.create(id=str(uuid4()), period_type="month",
                period_key="2026-08", platform="京东", shop_name="Synthetic",
                category="", manager="", created_at="synthetic", updated_at="synthetic")
        self.assertEqual((self.revision().revision, self.revision().source_digest), baseline)
        self.assertEqual(self.marker_count(), 0)

    def test_writer_role_cannot_write_marker_disable_trigger_or_call_definer(self):
        line = self.seed_line("writer-role")
        role = "fin_rev_guard_" + uuid4().hex[:12]
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE ROLE "{role}" NOLOGIN')
            cursor.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
            cursor.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON finance_lines TO "{role}"')
            cursor.execute(f'GRANT SELECT, UPDATE ON finance_data_revisions TO "{role}"')
            cursor.execute("SELECT prosecdef, proconfig FROM pg_proc WHERE oid="
                "'public.finance_source_mark_revision_required()'::regprocedure")
            security_definer, settings = cursor.fetchone()
            self.assertTrue(security_definer)
            self.assertIn("search_path=pg_catalog,public",
                [setting.replace(" ", "") for setting in settings])
            cursor.execute("SELECT has_table_privilege(%s,"
                "'public.finance_source_revision_markers','INSERT')", [role])
            self.assertFalse(cursor.fetchone()[0])
            cursor.execute("SELECT has_function_privilege(%s,"
                "'public.finance_source_mark_revision_required()','EXECUTE')", [role])
            self.assertFalse(cursor.fetchone()[0])
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(f'SET LOCAL ROLE "{role}"')
                for statement in (
                    "INSERT INTO public.finance_source_revision_markers VALUES (1,0,'" + "0"*64 + "')",
                    "ALTER TABLE public.finance_lines DISABLE TRIGGER finance_line_revision_required",
                ):
                    with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
                        cursor.execute(statement)
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE public.finance_lines SET amount_cents=201 WHERE id=%s",
                        [line.id])
                    cursor.execute("UPDATE public.finance_data_revisions SET "
                        "revision=revision+1,source_digest=%s WHERE domain='finance'",
                        ["b" * 64])
            self.assertEqual(FinanceLine.objects.get(pk=line.pk).amount_cents, 201)
            self.assertEqual(self.revision().source_digest, "b" * 64)
            self.assertEqual(self.marker_count(), 0)
            with connection.cursor() as cursor:
                cursor.execute(f'REVOKE UPDATE ON finance_lines FROM "{role}"')
            with self.assertRaises(DatabaseError):
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute(f'SET LOCAL ROLE "{role}"')
                        cursor.execute("UPDATE public.finance_lines SET amount_cents=202 WHERE id=%s",
                            [line.id])
        finally:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP OWNED BY "{role}"')
                cursor.execute(f'DROP ROLE "{role}"')

    def test_two_connections_serialize_on_revision_row(self):
        first, second = self.seed_line("first"), self.seed_line("second")
        baseline = self.revision().revision

        def competing_update():
            close_old_connections()
            try:
                with transaction.atomic(using="default"):
                    with connections["default"].cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '250ms'")
                    FinanceLine.objects.filter(pk=second.pk).update(amount_cents=202)
                    self.bump("competing")
            finally:
                connections["default"].close()

        with transaction.atomic():
            FinanceLine.objects.filter(pk=first.pk).update(amount_cents=101)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(competing_update)
                with self.assertRaises(DatabaseError):
                    future.result(timeout=10)
            self.bump("first")
        self.assertEqual(self.revision().revision, baseline + 1)
        self.assertEqual(FinanceLine.objects.get(pk=second.pk).amount_cents, 100)
        with transaction.atomic():
            FinanceLine.objects.filter(pk=second.pk).update(amount_cents=202)
            self.bump("second")
        self.assertEqual(self.revision().revision, baseline + 2)
        self.assertEqual(self.marker_count(), 0)

    def test_versioned_direct_write_invalidates_real_owning_continuation(self):
        role, _ = AccessRole.objects.get_or_create(code="admin",
            defaults={"rank": 40, "label": "Admin"})
        now = timezone.now()
        actor = Principal("finance-guard@example.invalid", "Synthetic", "admin", None)
        AppUser.objects.create(email=actor.email, role=role, status="active",
            scope=None, display_name="Synthetic", version=1,
            created_at=now, updated_at=now)
        import_finance_payload(prepared_payload("2026-08", "2026-09"), actor.email)
        base = FinanceLine.objects.filter(month="2026-08", scope_key="business",
            section="summary").first()
        self.assertIsNotNone(base)
        values = {field.attname: getattr(base, field.attname)
            for field in base._meta.concrete_fields if field.attname != "id"}
        with transaction.atomic():
            FinanceLine.objects.bulk_create([FinanceLine(**{**values,
                "subject_name": f"Synthetic extra {index}"}) for index in range(105)])
            self.bump("more-pages")
        query = {"months": ["2026-08", "2026-09"],
            "scope": {"scope_key": "business", "scope_type": "business",
                "scope_name": "志高事业部", "group_name": ""},
            "analysisPeriod": {"startDate": "2026-08-20",
                "endDate": "2026-09-18"}}
        first = read_page(actor, query)
        self.assertIsNotNone(first["pagination"]["nextOffset"])
        with transaction.atomic():
            FinanceLine.objects.filter(pk=first["rows"][0]["id"]).update(amount_cents=777)
            self.bump("changed-after-first-page")
        with self.assertRaisesRegex(FinanceApiError, "版本或发布批次已变化"):
            read_page(actor, query, offset=first["pagination"]["nextOffset"],
                after_id=first["pagination"]["nextLastId"],
                expected_source_ref=first["sourceRef"],
                expected_revision=first["sourceRevision"])

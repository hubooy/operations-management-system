"""Future raw-byte sidecar: no legacy finance row mutation or mapping claim."""
from __future__ import annotations

import hashlib
import json
import re
from unittest.mock import patch

from django import test as djtest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone
import psycopg
from psycopg import sql

from access_control.models import AccessRole, AppUser
from finance import (import_service, raw_column_evidence_v2 as column_owner,
    raw_workbook_attestation_v2 as owner,
    workbook_bytes_v2, workbook_column_evidence_v2)
from finance.errors import FinanceApiError
from finance.models import (FinanceDataRevision, FinanceImportBatch,
    FinanceLine, FinanceMonth, FinanceRawEvidenceMonth,
    FinanceRawWorkbookAttestation, FinanceRawWorkbookColumn,
    FinanceRawWorkbookCell)
from sales.auth import Principal
from sales.tests.factories import TEST_SECRET, signed_headers

from .test_workbook_bytes_v2 import _xlsx
from importlib import import_module


MIGRATION = import_module("finance.migrations.0006_raw_workbook_bytes_v2")


@djtest.override_settings(DJANGO_ENVIRONMENT="test",
    DJANGO_PROCESS_ROLE="development")
class RawWorkbookAttestationTests(djtest.TestCase):
    @classmethod
    def setUpClass(cls):
        if connection.vendor == "postgresql":
            connection.ensure_connection()
            with connection.cursor() as cursor:
                for role in MIGRATION.APP_ROLES:
                    cursor.execute("SELECT to_regrole(%s)", [role])
                    if cursor.fetchone()[0] is None:
                        cursor.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT "
                            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
                            "NOBYPASSRLS").format(sql.Identifier(role)))
                cursor.execute("SELECT to_regrole('teruisi_finance_raw_probe')")
                if cursor.fetchone()[0] is None:
                    cursor.execute("CREATE ROLE teruisi_finance_raw_probe "
                        "NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOREPLICATION NOBYPASSRLS")
        super().setUpClass()

    def setUp(self):
        self.raw = _xlsx()
        parsed = workbook_bytes_v2.parse_single_month_xlsx(self.raw)
        self.candidate = workbook_column_evidence_v2.build_candidate(parsed,
            "synthetic.xlsx")
        month = self.candidate["months"][0]
        AccessRole.objects.get_or_create(code="admin", defaults={
            "label": "admin", "description": "admin", "rank": 3,
            "permissions": []})
        now = timezone.now()
        AppUser.objects.create(email="bytes-finance@example.invalid",
            display_name="bytes-finance", role_id="admin", scope=None,
            created_at=now, updated_at=now)
        self.actor = Principal("bytes-finance@example.invalid",
            "bytes-finance", "admin", None)
        _, content_hash, row_count = import_service._fingerprint([month])
        self.batch = FinanceImportBatch.objects.create(
            id="synthetic-bytes-batch", source="合成财报",
            file_name=self.candidate["fileName"],
            file_size_bytes=self.candidate["fileSizeBytes"],
            file_hash="f" * 64, raw_file_hash=self.candidate["rawFileHash"],
            content_hash=content_hash, scope_key="s" * 64,
            published_state_token="a" * 64, status="completed",
            row_count=row_count, inserted_count=row_count,
            parsed_month_count=1, imported_month_count=1,
            months_json=[month["month"]], actor_email=self.actor.email,
            created_at=now.isoformat(), completed_at=now.isoformat())
        FinanceMonth.objects.create(month=month["month"],
            batch_id=self.batch.id, sheet_name=month["sheetName"],
            business_name=month["businessName"],
            source_file_name=self.candidate["fileName"],
            status="completed", shop_count=month["shopCount"],
            subject_count=month["subjectCount"],
            imported_at=now.isoformat())
        FinanceLine.objects.bulk_create([import_service._line_model(line,
            now.isoformat()) for line in month["lines"]])
        FinanceDataRevision.objects.update_or_create(domain="finance",
            defaults={"revision": 1, "source_digest": "a" * 64})

    def old_snapshot(self):
        all_rows = {"month": list(FinanceMonth.objects.order_by("pk").values()),
            "batch": list(FinanceImportBatch.objects.order_by("pk").values()),
            "lines": list(FinanceLine.objects.order_by("pk").values())}
        return hashlib.sha256(json.dumps(all_rows, default=str,
            sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")).hexdigest()

    def test_bytes_add_both_sidecars_without_touching_old_rows(self):
        with self.assertRaises(FinanceApiError):
            owner.stage(self.actor, self.raw, "2026-01", self.batch.id,
                "a" * 64)
        before = self.old_snapshot()
        result = owner.stage(self.actor, self.raw, "2026-01",
            self.batch.id, "a" * 64, enabled=True)
        self.assertTrue(result["backendRawBytesObserved"])
        self.assertFalse(result["stableNetshopShopIdentityVerified"])
        self.assertTrue(result["crossGroupSameNameRisk"])
        self.assertEqual(FinanceRawEvidenceMonth.objects.count(), 1)
        self.assertEqual(FinanceRawWorkbookAttestation.objects.count(), 1)
        self.assertEqual((FinanceRawWorkbookColumn.objects.count(),
            FinanceRawWorkbookCell.objects.count()), (5, 20))
        shop = list(FinanceRawWorkbookColumn.objects.filter(
            scope_type="shop").order_by("column_index").values_list(
            "column_index", "group_name"))
        self.assertEqual(shop, [(3, "京东组"), (5, "天猫组")])
        values = list(FinanceRawWorkbookCell.objects.filter(
            metric_key="gross_sales", column__scope_type="shop")
            .order_by("column_index").values_list("amount_cents", flat=True))
        self.assertEqual(values, [10_000, 20_000])
        legacy = FinanceLine.objects.get(month="2026-01",
            scope_key="shop:同名店", metric_key="gross_sales")
        self.assertEqual(sum(values), legacy.amount_cents)
        self.assertEqual(self.old_snapshot(), before)
        self.assertTrue(owner.stage(self.actor, self.raw, "2026-01",
            self.batch.id, "a" * 64, enabled=True)["idempotentReplay"])
        self.assertEqual(owner.read(self.actor, "2026-01", enabled=True)[
            "rawFileSha256"], result["rawFileSha256"])

    def test_changed_bytes_or_state_never_attach(self):
        with self.assertRaises(FinanceApiError):
            owner.stage(self.actor, self.raw, "2026-01",
                "another-batch", "a" * 64, enabled=True)
        with self.assertRaises(FinanceApiError):
            owner.stage(self.actor, _xlsx(amount=101), "2026-01",
                self.batch.id, "a" * 64, enabled=True)
        with self.assertRaises(FinanceApiError):
            owner.stage(self.actor, self.raw, "2026-01",
                self.batch.id, "b" * 64, enabled=True)
        self.assertFalse(FinanceRawWorkbookAttestation.objects.exists())
        result = owner.stage(self.actor, self.raw, "2026-01",
            self.batch.id, "a" * 64, enabled=True)
        FinanceDataRevision.objects.filter(domain="finance").update(
            revision=2, source_digest="b" * 64)
        with self.assertRaises(FinanceApiError):
            owner.read(self.actor, "2026-01", enabled=True)
        self.assertEqual(FinanceRawWorkbookAttestation.objects.count(), 1)

    def test_existing_different_0005_rejected_without_0006(self):
        now = timezone.now()
        FinanceRawEvidenceMonth.objects.create(
            id="0" * 64, month="2026-01", batch=self.batch,
            finance_revision=1, finance_source_digest="a" * 64,
            raw_file_hash=self.batch.raw_file_hash,
            batch_content_hash=self.batch.content_hash,
            batch_published_state_token="a" * 64,
            candidate_digest="0" * 64, evidence_digest="0" * 64,
            header_digest="0" * 64, cells_digest="0" * 64,
            column_chain_digest="0" * 64,
            cell_chain_digest="0" * 64,
            collision_digest="0" * 64,
            column_count=1, cell_count=1,
            cross_group_same_name_risk=False,
            ambiguity_flags_json=[])
        with self.assertRaises(FinanceApiError):
            owner.stage(self.actor, self.raw, "2026-01",
                self.batch.id, "a" * 64, enabled=True)
        self.assertFalse(FinanceRawWorkbookAttestation.objects.exists())

    def test_existing_exact_0005_can_be_completed_by_the_original_bytes(self):
        before = self.old_snapshot()
        column_owner.stage(self.actor, self.candidate, enabled=True)
        self.assertEqual(FinanceRawEvidenceMonth.objects.count(), 1)
        self.assertFalse(FinanceRawWorkbookAttestation.objects.exists())
        attached = owner.stage(self.actor, self.raw, "2026-01",
            self.batch.id, "a" * 64, enabled=True)
        self.assertTrue(attached["backendRawBytesObserved"])
        self.assertEqual(FinanceRawEvidenceMonth.objects.count(), 1)
        self.assertEqual(FinanceRawWorkbookAttestation.objects.count(), 1)
        self.assertEqual(self.old_snapshot(), before)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_signed_private_binary_route_is_closed_by_default(self):
        path = ("/api/finance/imports/raw-attest?month=2026-01&"
            "batchId=synthetic-bytes-batch")
        options = {"data": self.raw,
            "content_type": "application/octet-stream",
            "headers": signed_headers(path, method="POST", body=self.raw,
                request_id="finance-bytes-closed", email=self.actor.email)}
        response = self.client.post(path, **options)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(FinanceRawEvidenceMonth.objects.exists())
        with djtest.override_settings(FINANCE_RAW_WORKBOOK_BYTES_V2_ENABLED=True):
            options["headers"] = signed_headers(path, method="POST",
                body=self.raw, request_id="finance-bytes-open",
                email=self.actor.email)
            accepted = self.client.post(path, **options)
            self.assertEqual(accepted.status_code, 201, accepted.content)
            replay = self.client.post(path, **options)
            self.assertEqual(replay.status_code, 201)
            self.assertEqual(replay["X-Teruisi-Write-Replay"], "1")
            self.assertEqual(FinanceRawWorkbookAttestation.objects.count(), 1)

    def test_real_role_catalog_and_immutable_rows(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires isolated PostgreSQL finance.0006")
        owner.stage(self.actor, self.raw, "2026-01", self.batch.id,
            "a" * 64, enabled=True)
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)
        for table in MIGRATION.TABLES:
            for statement in ("UPDATE public." + table +
                    " SET id=id", "DELETE FROM public." + table,
                    "TRUNCATE public." + table):
                with self.subTest(statement=statement), \
                        self.assertRaises(DatabaseError):
                    with transaction.atomic(), connection.cursor() as cursor:
                        cursor.execute(statement)
        settings = connection.settings_dict
        with psycopg.connect(host=settings["HOST"], port=settings["PORT"],
                dbname=settings["NAME"], user=settings["USER"],
                password=settings["PASSWORD"], autocommit=True) as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_finance_writer")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("INSERT INTO public.finance_raw_workbook_attestations "
                    "(id) VALUES ('direct-writer-rejected')")

    def test_catalog_refuses_public_third_role_and_owner_drift(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires isolated PostgreSQL finance.0006")
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)
        for sql_text in (
                "GRANT SELECT ON public.finance_raw_workbook_attestations TO PUBLIC",
                "GRANT INSERT ON public.finance_raw_workbook_columns "
                "TO teruisi_finance_raw_probe",
                "ALTER FUNCTION public.finance_raw_workbook_immutable() "
                "OWNER TO teruisi_finance_raw_probe"):
            with self.subTest(sql=sql_text), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(sql_text)
                    with self.assertRaises(RuntimeError):
                        MIGRATION.verify_catalog(cursor)
                transaction.set_rollback(True)

    def test_catalog_refuses_same_name_check_unique_fk_and_column_drift(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires isolated PostgreSQL finance.0006")
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)
            cursor.execute("SELECT conname FROM pg_catalog.pg_constraint "
                "WHERE conrelid='public.finance_raw_workbook_columns'::regclass "
                "AND contype='f' AND pg_catalog.pg_get_constraintdef(oid) "
                "LIKE 'FOREIGN KEY (evidence_column_id)%'")
            fk_name = cursor.fetchone()[0]
        if not re.fullmatch(r"[a-z0-9_]+", fk_name):
            self.fail("unexpected isolated FK name")
        mutations = (
            ("ALTER TABLE public.finance_raw_workbook_attestations "
             "DROP CONSTRAINT fin_workbook_bytes_observed",
             "ALTER TABLE public.finance_raw_workbook_attestations "
             "ADD CONSTRAINT fin_workbook_bytes_observed "
             "CHECK (raw_workbook_bytes_observed OR TRUE)"),
            ("ALTER TABLE public.finance_raw_workbook_cells "
             "DROP CONSTRAINT fin_workbook_cell_uq",
             "ALTER TABLE public.finance_raw_workbook_cells "
             "ADD CONSTRAINT fin_workbook_cell_uq UNIQUE "
             "(attestation_id,section,row_index,column_index,subject_name)"),
            ("ALTER TABLE public.finance_raw_workbook_columns "
             f"DROP CONSTRAINT {fk_name}",
             "ALTER TABLE public.finance_raw_workbook_columns "
             f"ADD CONSTRAINT {fk_name} FOREIGN KEY "
             "(evidence_column_id) REFERENCES public."
             "finance_raw_column_evidence_cells(id) "
             "DEFERRABLE INITIALLY DEFERRED"),
            ("ALTER TABLE public.finance_raw_workbook_columns "
             "ALTER COLUMN scope_name TYPE varchar(1001)",),
        )
        for statements in mutations:
            with self.subTest(statements=statements), transaction.atomic():
                with connection.cursor() as cursor:
                    for statement in statements:
                        cursor.execute(statement)
                    with self.assertRaises(RuntimeError):
                        MIGRATION.verify_catalog(cursor)
                transaction.set_rollback(True)

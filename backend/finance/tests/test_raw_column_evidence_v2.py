"""Isolated PostgreSQL tests for the closed finance raw-column digest sidecar."""
from copy import deepcopy
from importlib import import_module
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from django import test as djtest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone
import psycopg
from psycopg import sql

from access_control.models import AccessRole, AppUser
from finance import import_service, raw_column_evidence_v2 as service
from finance.errors import FinanceApiError
from finance.models import (FinanceDataRevision, FinanceImportBatch,
    FinanceLine, FinanceMonth, FinanceRawEvidenceCell,
    FinanceRawEvidenceColumn, FinanceRawEvidenceMonth)
from sales.auth import Principal


FIXTURE = (Path(__file__).resolve().parent / "fixtures" /
    "raw_column_v2_candidate.json")
MIGRATION = import_module("finance.migrations.0005_raw_column_evidence_v2")


@djtest.override_settings(DJANGO_ENVIRONMENT="test",
    DJANGO_PROCESS_ROLE="development")
class RawColumnEvidenceV2Tests(djtest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Synthetic roles are created only in the isolated test database,
        # before TestCase's class transaction, so a second real connection can
        # probe their direct SQL privileges.
        if connection.vendor == "postgresql":
            connection.ensure_connection()
            with connection.cursor() as cursor:
                for role in MIGRATION.APP_ROLES:
                    cursor.execute("SELECT to_regrole(%s)", [role])
                    if cursor.fetchone()[0] is None:
                        cursor.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT "
                            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
                            "NOBYPASSRLS").format(sql.Identifier(role)))
        super().setUpClass()

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires isolated PostgreSQL finance.0005")
        self.candidate = json.loads(FIXTURE.read_text(encoding="utf-8"))
        month = self.candidate["months"][0]
        AccessRole.objects.get_or_create(code="admin", defaults={
            "label": "admin", "description": "admin", "rank": 3,
            "permissions": []})
        now = timezone.now()
        AppUser.objects.create(email="raw-finance@example.invalid",
            display_name="raw-finance", role_id="admin", scope=None,
            created_at=now, updated_at=now)
        self.actor = Principal("raw-finance@example.invalid", "raw-finance",
            "admin", None)
        _, content_hash, row_count = import_service._fingerprint([month])
        self.batch = FinanceImportBatch.objects.create(
            id="synthetic-raw-batch", source="合成财报", file_name=self.candidate["fileName"],
            file_size_bytes=self.candidate["fileSizeBytes"],
            file_hash="f" * 64, raw_file_hash=self.candidate["rawFileHash"],
            content_hash=content_hash, scope_key="s" * 64,
            published_state_token="a" * 64, status="completed",
            row_count=row_count, inserted_count=row_count,
            parsed_month_count=1, imported_month_count=1,
            months_json=[month["month"]], actor_email=self.actor.email,
            created_at=now.isoformat(), completed_at=now.isoformat())
        FinanceMonth.objects.create(month=month["month"], batch_id=self.batch.id,
            sheet_name=month["sheetName"], business_name=month["businessName"],
            source_file_name=self.candidate["fileName"], status="completed",
            shop_count=month["shopCount"], subject_count=month["subjectCount"],
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

    def test_complete_digest_candidate_is_append_only_and_legacy_rows_unchanged(self):
        with self.assertRaises(FinanceApiError):
            service.stage(self.actor, self.candidate)
        before = self.old_snapshot()
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)
        value = service.stage(self.actor, self.candidate, enabled=True)
        self.assertEqual((value["columnCount"], value["cellCount"]), (3, 9))
        self.assertTrue(value["crossGroupSameNameRisk"])
        self.assertFalse(value["rawWorkbookBytesIndependentlyVerified"])
        self.assertFalse(value["stableNetshopShopIdentityVerified"])
        self.assertFalse(value["mappingAuthorityVerified"])
        self.assertTrue(value["singleMonthBatchOnly"])
        self.assertEqual(FinanceRawEvidenceMonth.objects.count(), 1)

        forged_header = deepcopy(self.candidate)
        forged_header["columnEvidence"][0]["headerCells"][2][
            "rawGroupCell"] = None
        forged_header["columnEvidence"][0]["columns"][2][
            "rawGroupCell"] = None
        forged_header["columnEvidence"][0]["evidenceDigest"] = service._hash({
            key: value for key, value in forged_header["columnEvidence"][0].items()
            if key != "evidenceDigest"})
        forged_header["candidateDigest"] = service._hash({key: value
            for key, value in forged_header.items() if key != "candidateDigest"})
        with self.assertRaises(FinanceApiError):
            service.stage(self.actor, forged_header, enabled=True)
        self.assertEqual(FinanceRawEvidenceColumn.objects.count(), 3)
        self.assertEqual(FinanceRawEvidenceCell.objects.count(), 9)
        self.assertEqual(self.old_snapshot(), before)
        reread = service.read(self.actor, "2026-08", enabled=True)
        self.assertEqual(reread["evidenceDigest"], value["evidenceDigest"])
        replay = service.stage(self.actor, self.candidate, enabled=True)
        self.assertTrue(replay["idempotentReplay"])
        self.assertEqual((FinanceRawEvidenceMonth.objects.count(),
            FinanceRawEvidenceCell.objects.count()), (1, 9))
        self.assertEqual(self.old_snapshot(), before)

    def test_partial_tampered_and_changed_candidate_are_rejected_without_rows(self):
        missing = deepcopy(self.candidate)
        missing["columnEvidence"][0]["cells"].pop()
        missing["columnEvidence"][0]["cellCount"] -= 1
        missing["columnEvidence"][0]["evidenceDigest"] = service._hash({
            key: value for key, value in missing["columnEvidence"][0].items()
            if key != "evidenceDigest"})
        missing["candidateDigest"] = service._hash({key: value
            for key, value in missing.items() if key != "candidateDigest"})
        with self.assertRaises(FinanceApiError):
            service.stage(self.actor, missing, enabled=True)
        bad = deepcopy(self.candidate)
        bad["candidateDigest"] = "0" * 64
        with self.assertRaises(FinanceApiError):
            service.stage(self.actor, bad, enabled=True)
        self.assertEqual(FinanceRawEvidenceMonth.objects.count(), 0)
        service.stage(self.actor, self.candidate, enabled=True)
        changed = deepcopy(self.candidate)
        changed["columnEvidence"][0]["cells"][0]["rawValue"] += " "
        changed["columnEvidence"][0]["evidenceDigest"] = service._hash({
            key: value for key, value in changed["columnEvidence"][0].items()
            if key != "evidenceDigest"})
        changed["candidateDigest"] = service._hash({key: value
            for key, value in changed.items() if key != "candidateDigest"})
        with self.assertRaises(FinanceApiError):
            service.stage(self.actor, changed, enabled=True)
        changed_claim = deepcopy(self.candidate)
        changed_claim["warnings"].append({"code": "SYNTHETIC_WARNING",
            "message": "同一规范行的另一个候选声明"})
        changed_claim["candidateDigest"] = service._hash({key: value
            for key, value in changed_claim.items() if key != "candidateDigest"})
        with self.assertRaises(FinanceApiError):
            service.stage(self.actor, changed_claim, enabled=True)
        self.assertEqual(FinanceRawEvidenceMonth.objects.count(), 1)

    def test_revision_staleness_and_direct_mutation_guards(self):
        service.stage(self.actor, self.candidate, enabled=True)
        FinanceDataRevision.objects.filter(domain="finance").update(
            revision=2, source_digest="b" * 64)
        with self.assertRaises(FinanceApiError):
            service.read(self.actor, "2026-08", enabled=True)
        with self.assertRaises(FinanceApiError):
            service.stage(self.actor, self.candidate, enabled=True)
        for query in (
                "UPDATE finance_raw_column_evidence_months SET month=month",
                "DELETE FROM finance_raw_column_evidence_columns",
                "TRUNCATE finance_raw_column_evidence_cells"):
            with self.subTest(query=query), self.assertRaises(DatabaseError):
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(query)
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)

    def test_extra_child_insert_prevents_idempotent_replay(self):
        service.stage(self.actor, self.candidate, enabled=True)
        row = FinanceRawEvidenceMonth.objects.get()
        FinanceRawEvidenceCell.objects.create(evidence=row, section="summary",
            row_index=900, column_index=2, cell_digest="d" * 64)
        with self.assertRaises(FinanceApiError):
            service.stage(self.actor, self.candidate, enabled=True)
        with self.assertRaises(FinanceApiError):
            service.read(self.actor, "2026-08", enabled=True)

    def test_current_month_reimport_batch_identity_invalidates_old_sidecar(self):
        service.stage(self.actor, self.candidate, enabled=True)
        successor = FinanceImportBatch.objects.get(pk=self.batch.id)
        successor.pk = "synthetic-successor-batch"
        successor.file_hash = "e" * 64
        successor.raw_file_hash = "d" * 64
        successor.published_state_token = "c" * 64
        successor.save(force_insert=True)
        FinanceMonth.objects.filter(pk="2026-08").update(batch_id=successor.pk)
        with self.assertRaises(FinanceApiError):
            service.read(self.actor, "2026-08", enabled=True)
        with self.assertRaises(FinanceApiError):
            service.stage(self.actor, self.candidate, enabled=True)

    def test_guard_event_acl_and_owner_catalog_drift_are_detected(self):
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)
        with self.assertRaises(RuntimeError):
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("DROP TRIGGER fin_raw_evidence_immutable_row "
                    "ON finance_raw_column_evidence_months")
                cursor.execute("CREATE TRIGGER fin_raw_evidence_immutable_row "
                    "BEFORE INSERT ON finance_raw_column_evidence_months "
                    "FOR EACH ROW EXECUTE FUNCTION " + MIGRATION.GUARD)
                MIGRATION.verify_catalog(cursor)
        with self.assertRaises(RuntimeError):
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("GRANT EXECUTE ON FUNCTION " + MIGRATION.GUARD +
                    " TO PUBLIC")
                MIGRATION.verify_catalog(cursor)
        with self.assertRaises(RuntimeError):
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("GRANT INSERT ON TABLE "
                    "finance_raw_column_evidence_cells "
                    "TO teruisi_finance_writer")
                MIGRATION.verify_catalog(cursor)
        with self.assertRaises(RuntimeError):
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("GRANT INSERT (cell_digest) ON TABLE "
                    "finance_raw_column_evidence_cells "
                    "TO teruisi_finance_writer")
                MIGRATION.verify_catalog(cursor)
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)

    def test_application_roles_have_no_direct_sidecar_insert(self):
        with connection.cursor() as cursor:
            for table in MIGRATION.TABLES:
                for role in MIGRATION.APP_ROLES:
                    cursor.execute("SELECT to_regrole(%s)", [role])
                    self.assertIsNotNone(cursor.fetchone()[0])
                    cursor.execute("SELECT has_table_privilege(%s,%s,'INSERT')",
                        [role, "public." + table])
                    self.assertEqual(cursor.fetchone(), (False,))
        config = connection.settings_dict
        with psycopg.connect(host=config["HOST"], port=config["PORT"],
                dbname=config["NAME"], user=config["USER"],
                password=config["PASSWORD"], autocommit=True) as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_finance_writer")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("INSERT INTO public.finance_raw_column_evidence_cells "
                    "DEFAULT VALUES")

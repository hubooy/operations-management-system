"""Isolated PostgreSQL read-only v4 admission over mixed immutable segments."""
from importlib import import_module
from unittest.mock import patch
from uuid import uuid4
import psycopg

from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from access_control.models import AppUser
from business_analysis.contracts import digest
from finance.models import FinanceDataRevision, FinanceWriteAuthority
from netshop.models import NetshopDataRevision, NetshopWriteAuthority

from . import (business_v4_seal_admission as admission,
    business_v4_validation as validation, models as m)
from .control_models import AiDataRevision, AiWriteAuthority
from .test_business_v4_validation import BusinessV4ValidationTests as fixture
from .policy import AiError


class BusinessV4SealAdmissionTests(TransactionTestCase):
    promotion_owner = fixture.promotion_owner
    rebuild_plan = fixture.rebuild_plan
    finance_owner = fixture.finance_owner
    owner = fixture.owner
    collect = fixture.collect
    complete_mixed = fixture.complete_mixed

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("v4 admission needs PostgreSQL source row locks")
        AiDataRevision.objects.get_or_create(domain="ai-assistant",
            defaults={"revision": 0, "source_digest": "0" * 64})
        authority_fields = {
            "status": "postgres", "authority_epoch": uuid4(),
            "cutover_id": "v4-admission-isolated", "migration_verify_run_id": "v4-admission-isolated",
            "activated_at": timezone.now()}
        authority, created = AiWriteAuthority.objects.get_or_create(id=1,
            defaults=authority_fields)
        if not created and authority.status == "d1":
            AiWriteAuthority.objects.filter(pk=authority.pk).update(
                **authority_fields)
        elif not created and authority.status != "postgres":
            self.fail("unexpected AI authority state in isolated fixture")
        FinanceWriteAuthority.objects.update_or_create(id=1,
            defaults={"status": "postgres"})
        FinanceDataRevision.objects.get_or_create(domain="finance",
            defaults={"revision": 0, "source_digest": "0" * 64})
        NetshopWriteAuthority.objects.update_or_create(id=1, defaults={
            "status": "postgres", "authority_epoch": uuid4(),
            "cutover_id": "v4-admission-isolated",
            "migration_verify_run_id": "v4-admission-isolated",
            "activated_at": timezone.now()})
        # The legacy synthetic owning fixture directly inserts netshop rows.
        # Publish its final global revision in the same real transaction so
        # the new source-write marker passes without a test-only bypass.
        with transaction.atomic():
            fixture.setUp(self)
            current = NetshopDataRevision.objects.select_for_update().get(
                domain="netshop")
            NetshopDataRevision.objects.filter(domain="netshop").update(
                revision=current.revision + 1,
                source_digest=digest([current.source_digest,
                    "v4-admission-fixture", current.revision + 1]))
            with connection.cursor() as cursor:
                cursor.execute("SELECT baseline_revision,baseline_digest FROM "
                    "public.netshop_source_revision_markers "
                    "WHERE transaction_id=txid_current()")
                marker = cursor.fetchone()
            final = NetshopDataRevision.objects.get(domain="netshop")
            self.assertIsNotNone(marker)
            self.assertGreater(final.revision, marker[0])
            self.assertNotEqual(final.source_digest, marker[1])
        self.created_roles = []
        with connection.cursor() as cursor:
            for role in ("teruisi_ai_writer", "teruisi_ai_reader",
                         "teruisi_finance_writer", "teruisi_netshop_writer"):
                cursor.execute("SELECT to_regrole(%s)", [role])
                if cursor.fetchone()[0] is None:
                    cursor.execute(f'CREATE ROLE "{role}" NOLOGIN NOINHERIT '
                        'NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS')
                    self.created_roles.append(role)
            cursor.execute("GRANT EXECUTE ON FUNCTION "
                "public.ai_v4_lock_source_revisions_for_admission() "
                "TO teruisi_ai_writer")
            cursor.execute("GRANT USAGE ON SCHEMA public TO "
                "teruisi_ai_writer,teruisi_ai_reader")

    def tearDown(self):
        with connection.cursor() as cursor:
            for role in reversed(self.created_roles):
                cursor.execute(f'DROP OWNED BY "{role}"')
                cursor.execute(f'DROP ROLE "{role}"')

    def attempt(self):
        version = self.complete_mixed()
        prepared = validation.start_attempt(self.parent.id, version, self.principal)
        for source_key in ("finance-context", "promotion-current"):
            result = validation.advance_segment(prepared["attemptId"],
                source_key, 1, self.principal)
            self.assertTrue(result["sourceCompleteCandidate"])
        return prepared["attemptId"]

    def test_mixed_complete_run_has_only_read_only_unsealed_candidate(self):
        attempt_id = self.attempt()
        before = (m.AiBusinessV4Run.objects.get(pk=self.parent.pk).version,
            m.AiBusinessV4ValidationSegment.objects.count(),
            m.AiBusinessV4Chunk.objects.count())
        result = admission.inspect(self.parent.id, attempt_id, self.principal)
        self.assertEqual(result["schemaVersion"], admission.SCHEMA)
        self.assertEqual(result["sourceCount"], 2)
        self.assertEqual({item["domain"] for item in result["sources"]},
            {"netshop", "finance"})
        self.assertTrue(result["sourceRevisionWriteFencesVerified"])
        self.assertTrue(result["segmentedReceiptAndRequestProofVerified"])
        self.assertTrue(all(item["revisionFreshness"] == "current_revision"
            for item in result["sources"]))
        self.assertFalse(result["sealed"])
        self.assertFalse(result["upstreamSignatureVerified"])
        self.assertFalse(result["reportGenerationSupported"])
        self.assertEqual((m.AiBusinessV4Run.objects.get(pk=self.parent.pk).version,
            m.AiBusinessV4ValidationSegment.objects.count(),
            m.AiBusinessV4Chunk.objects.count()), before)
        self.assertEqual(result["candidateDigest"], digest({key: value
            for key, value in result.items() if key != "candidateDigest"}))

    def test_later_global_revision_is_disclosed_as_historical_not_forged_current(self):
        attempt_id = self.attempt()
        finance = FinanceDataRevision.objects.get(domain="finance")
        netshop = NetshopDataRevision.objects.get(domain="netshop")
        FinanceDataRevision.objects.filter(pk=finance.pk).update(
            revision=finance.revision + 1, source_digest="b" * 64)
        NetshopDataRevision.objects.filter(pk=netshop.pk).update(
            revision=netshop.revision + 1, source_digest="c" * 64)
        result = admission.inspect(self.parent.id, attempt_id, self.principal)
        self.assertTrue(all(item["revisionFreshness"] == "historical_revision"
            for item in result["sources"]))
        self.assertFalse(result["sealed"])
        with self.assertRaises(AiError):
            admission._freshness("8:" + "a" * 12, "7:" + "b" * 12)
        with self.assertRaises(AiError):
            admission._freshness("8:" + "a" * 12, "8:" + "b" * 12)

    def test_missing_segment_fake_mac_revoked_actor_and_key_rotation_fail_closed(self):
        attempt_id = self.attempt()
        real_filter = m.AiBusinessV4ValidationSegment.objects.filter
        def omit(*args, **kwargs):
            return real_filter(*args, **kwargs).exclude(source__source_key="finance-context")
        with patch.object(m.AiBusinessV4ValidationSegment.objects, "filter",
                side_effect=omit), self.assertRaises(AiError):
            admission.inspect(self.parent.id, attempt_id, self.principal)
        with patch.object(validation, "_mac", return_value="0" * 64), \
                self.assertRaises(AiError):
            admission.inspect(self.parent.id, attempt_id, self.principal)
        AppUser.objects.filter(email=self.principal.email).update(status="inactive")
        with self.assertRaises(AiError):
            admission.inspect(self.parent.id, attempt_id, self.principal)
        AppUser.objects.filter(email=self.principal.email).update(status="active")
        with override_settings(DJANGO_INTERNAL_SECRET="rotated-key-" + "x" * 48), \
                self.assertRaises(AiError):
            admission.inspect(self.parent.id, attempt_id, self.principal)

    def test_disabled_source_trigger_and_excessive_writer_privilege_reject(self):
        attempt_id = self.attempt()
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE public.finance_lines DISABLE TRIGGER "
                    "finance_line_revision_required")
            with self.assertRaises(DatabaseError):
                admission.inspect(self.parent.id, attempt_id, self.principal)
            transaction.set_rollback(True)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("DROP TRIGGER finance_line_revision_required "
                    "ON public.finance_lines")
                cursor.execute("CREATE TRIGGER finance_line_revision_required "
                    "BEFORE INSERT OR UPDATE OR DELETE ON public.finance_lines "
                    "FOR EACH STATEMENT EXECUTE FUNCTION "
                    "public.finance_revision_monotonic_guard()")
            with self.assertRaises(DatabaseError):
                admission.inspect(self.parent.id, attempt_id, self.principal)
            transaction.set_rollback(True)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("GRANT INSERT ON public.finance_source_revision_markers "
                    "TO teruisi_finance_writer")
            with self.assertRaises(DatabaseError):
                admission.inspect(self.parent.id, attempt_id, self.principal)
            transaction.set_rollback(True)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("GRANT INSERT(transaction_id) ON "
                    "public.netshop_source_revision_markers TO teruisi_netshop_writer")
            with self.assertRaises(DatabaseError):
                admission.inspect(self.parent.id, attempt_id, self.principal)
            transaction.set_rollback(True)
        self.assertFalse(admission.inspect(self.parent.id, attempt_id,
            self.principal)["sealed"])

    def test_narrow_function_acl_and_no_direct_business_write_grants(self):
        with connection.cursor() as cursor:
            function = "public.ai_v4_lock_source_revisions_for_admission()"
            cursor.execute("SELECT has_function_privilege('teruisi_ai_writer',%s,'EXECUTE'),"
                "has_function_privilege('teruisi_ai_reader',%s,'EXECUTE')",
                [function, function])
            self.assertEqual(cursor.fetchone(), (True, False))
            cursor.execute("SELECT has_table_privilege('teruisi_ai_writer',"
                "'public.finance_data_revisions','UPDATE'),"
                "has_table_privilege('teruisi_ai_writer',"
                "'public.netshop_data_revisions','UPDATE'),"
                "has_table_privilege('teruisi_ai_writer',"
                "'public.finance_source_revision_markers','SELECT')")
            self.assertEqual(cursor.fetchone(), (False, False, False))
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE teruisi_ai_reader")
                cursor.execute("SELECT * FROM "
                    "public.ai_v4_lock_source_revisions_for_admission()")

    def test_real_session_authorization_writer_epoch_and_reader_rejection(self):
        db_settings = connection.settings_dict
        arguments = {"host": db_settings["HOST"], "port": db_settings["PORT"],
            "dbname": db_settings["NAME"], "user": db_settings["USER"],
            "password": db_settings["PASSWORD"], "autocommit": True}
        authority = AiWriteAuthority.objects.get(id=1)
        with psycopg.connect(**arguments) as writer:
            writer.execute("SET SESSION AUTHORIZATION teruisi_ai_writer")
            writer.execute("BEGIN")
            writer.execute("SELECT set_config('teruisi.ai_epoch',%s,true),"
                "set_config('teruisi.ai_cutover',%s,true)",
                [str(authority.authority_epoch), authority.cutover_id])
            row = writer.execute("SELECT finance_revision,netshop_revision,guard_version "
                "FROM public.ai_v4_lock_source_revisions_for_admission()").fetchone()
            self.assertEqual(row[2], admission.GUARD_VERSION)
            writer.execute("ROLLBACK")
            writer.execute("BEGIN")
            writer.execute("SELECT set_config('teruisi.ai_epoch','wrong-epoch',true),"
                "set_config('teruisi.ai_cutover',%s,true)", [authority.cutover_id])
            with self.assertRaisesRegex(psycopg.Error,
                    "ai_v4_admission_authority_mismatch"):
                writer.execute("SELECT * FROM "
                    "public.ai_v4_lock_source_revisions_for_admission()")
            writer.execute("ROLLBACK")
        with psycopg.connect(**arguments) as reader:
            reader.execute("SET SESSION AUTHORIZATION teruisi_ai_reader")
            with self.assertRaises(psycopg.Error):
                reader.execute("SELECT * FROM "
                    "public.ai_v4_lock_source_revisions_for_admission()")

    def test_empty_admission_function_reverse_and_reinstall_is_fail_closed(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regprocedure('public.ai_v4_commit_seal("
                "text,text,bigint,text,text,text,text)')")
            if cursor.fetchone()[0] is not None:
                self.skipTest("0037逆装仅在0038封存角色改写锁源函数之前适用")
        migration = import_module("ai_assistant.migrations.0037_business_v4_seal_admission_read")
        with transaction.atomic(), connection.schema_editor(atomic=False) as editor:
            migration.uninstall(None, editor)
            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regprocedure("
                    "'public.ai_v4_lock_source_revisions_for_admission()')")
                self.assertIsNone(cursor.fetchone()[0])
            migration.install(None, editor)
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regprocedure("
                "'public.ai_v4_lock_source_revisions_for_admission()')")
            self.assertIsNotNone(cursor.fetchone()[0])

    def test_install_never_retroactively_accepts_future_dated_old_fact(self):
        attempt_id = self.attempt()
        self.assertTrue(m.AiBusinessV4ValidationAttempt.objects.filter(
            pk=attempt_id).exists())
        migration = import_module("ai_assistant.migrations.0037_business_v4_seal_admission_read")
        with transaction.atomic():
            # Disposable adversarial fixture: timestamp alone cannot prove a
            # page was collected under the write/revision guards. Roll back
            # both this physical mutation and the temporary trigger state.
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE public.ai_business_v4_chunks "
                    "DISABLE TRIGGER ai_immutable_v4")
                cursor.execute("ALTER TABLE public.ai_business_v4_chunks "
                    "DISABLE TRIGGER ai_v4_state")
                cursor.execute("UPDATE public.ai_business_v4_chunks "
                    "SET created_at=now()+interval '1 day' WHERE run_id=%s",
                    [self.parent.id])
            with connection.schema_editor(atomic=False) as editor:
                with self.assertRaisesRegex(RuntimeError, "不能安装0037"):
                    migration.install(None, editor)
            transaction.set_rollback(True)

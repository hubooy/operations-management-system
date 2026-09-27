"""Isolated PostgreSQL hard boundary for independent v4 seal DB identity."""
from importlib import import_module
import json

import psycopg
from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase

from . import (business_v4_seal_admission as admission,
    business_v4_validation as validation, models as m)
from .control_models import AiDataRevision, AiWriteAuthority
from .policy import canonical, digest
from .test_business_v4_seal_admission import BusinessV4SealAdmissionTests as fixture


class BusinessV4SealWriterGateTests(TransactionTestCase):
    promotion_owner = fixture.promotion_owner
    rebuild_plan = fixture.rebuild_plan
    finance_owner = fixture.finance_owner
    owner = fixture.owner
    collect = fixture.collect
    complete_mixed = fixture.complete_mixed
    attempt = fixture.attempt
    tearDown = fixture.tearDown

    def setUp(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regprocedure('public.ai_v4_issue_seal_ticket("
                "text,text,text,bigint,bigint,text,text)')")
            if cursor.fetchone()[0] is not None:
                self.skipTest("0038直达封存已由0041票据门禁关闭；历史行为由升级演练验收")
        fixture.setUp(self)

    def database(self):
        settings = connection.settings_dict
        return psycopg.connect(host=settings["HOST"], port=settings["PORT"],
            dbname=settings["NAME"], user=settings["USER"],
            password=settings["PASSWORD"], autocommit=True)

    def body(self, attempt_id):
        candidate = admission.inspect(self.parent.id, attempt_id, self.principal)
        sources = []
        for proof in candidate["sources"]:
            source = m.AiBusinessV4Source.objects.get(run=self.parent,
                source_key=proof["sourceKey"])
            query = json.loads(source.query_json)
            item = {key: proof[key] for key in ("sourceKey", "domain",
                "queryDigest", "sourceRef", "sourceRevision", "sourceVersion",
                "pageCount", "rowCount", "storedBytes", "segmentCount",
                "terminalSegmentDigest", "receiptChainDigest",
                "revisionFreshness", "liveRevision")}
            if source.domain == "finance":
                item.update(scope=query["scope"],
                    analysisPeriod=query["analysisPeriod"],
                    missingMonths=proof["missingMonths"])
            else:
                item.update(window=query["window"], coverage=proof["coverage"])
            sources.append(item)
        parent = m.AiBusinessV4Run.objects.get(pk=self.parent.pk)
        attempt = m.AiBusinessV4ValidationAttempt.objects.get(pk=attempt_id)
        return {"schemaVersion": "business-v4-parent-seal-internal-v1",
            "runId": parent.id, "attemptId": attempt.id,
            "evidenceVersion": parent.version + 1,
            "planDigest": parent.plan_digest,
            "directoryDigest": attempt.directory_digest,
            "actorVersion": attempt.actor_version,
            "keyId": attempt.key_id, "sourceCount": len(sources),
            "sources": sources, "crossDomainSnapshotAtomic": False,
            "financeDailyProrationAllowed": False,
            "inferSkuProfit": False,
            "sumOverlappingErpB2bAdsAllowed": False,
            "upstreamSignatureVerified": False,
            "reportGenerationSupported": False,
            "agentDispatchSupported": False,
            "humanReviewRequired": True}

    def call_as_sealer(self, body, *, mac="f" * 64, raw_body=None):
        authority = AiWriteAuthority.objects.get(id=1)
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_seal_writer")
            db.execute("BEGIN")
            db.execute("SELECT set_config('teruisi.ai_epoch',%s,true),"
                "set_config('teruisi.ai_cutover',%s,true)",
                [str(authority.authority_epoch), authority.cutover_id])
            raw = canonical(body) if raw_body is None else raw_body
            try:
                result = db.execute("SELECT run_id,evidence_version,sealed_digest "
                    "FROM public.ai_v4_commit_seal(%s,%s,%s,%s,%s,%s,%s)",
                    [body["runId"], body["attemptId"], body["evidenceVersion"] - 1,
                     raw, digest(raw), mac, body["keyId"]]).fetchone()
            except Exception:
                db.execute("ROLLBACK")
                raise
            db.execute("COMMIT")
            return result

    def test_true_sealer_session_can_atomically_seal_only_complete_mixed_run(self):
        attempt_id = self.attempt()
        body = self.body(attempt_id)
        before = AiDataRevision.objects.get(domain="ai-assistant").revision
        result = self.call_as_sealer(body)
        self.assertEqual(result, (self.parent.id, body["evidenceVersion"],
            digest(canonical(body))))
        parent = m.AiBusinessV4Run.objects.get(pk=self.parent.pk)
        self.assertEqual((parent.status, parent.collection_status,
            parent.version), ("sealed", "manual", body["evidenceVersion"]))
        self.assertEqual(m.AiBusinessV4Seal.objects.filter(run=parent).count(), 1)
        self.assertEqual(AiDataRevision.objects.get(
            domain="ai-assistant").revision, before + 1)
        self.assertEqual(m.AiBusinessV4Seal.objects.get(run=parent).body_json,
            canonical(body))
        # PostgreSQL authenticates the independent role and body shape. It
        # cannot verify this deliberately synthetic 64-hex HMAC; the future
        # sealer and every reader must do so before treating it as authority.
        self.assertEqual(m.AiBusinessV4Seal.objects.get(run=parent).body_mac,
            "f" * 64)
        migration = import_module("ai_assistant.migrations.0038_business_v4_seal_writer_gate")
        with connection.schema_editor() as editor:
            with self.assertRaisesRegex(RuntimeError, "不能逆迁移"):
                migration.uninstall(None, editor)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessV4Chunk.objects.create(id="post-seal-forbidden",
                run=parent, source=self.sources["promotion-current"], sequence=3,
                payload_json="{}", payload_digest=digest("{}"),
                source_ref="a" * 64, source_revision="1:a", row_count=0)
        with self.assertRaises(psycopg.Error):
            self.call_as_sealer(body)
        self.assertEqual(m.AiBusinessV4Seal.objects.filter(run=parent).count(), 1)

    def test_ordinary_writer_reader_and_no_login_gate_cannot_forge_sealed(self):
        attempt_id = self.attempt()
        body = self.body(attempt_id)
        with connection.cursor() as cursor:
            cursor.execute("SELECT rolcanlogin FROM pg_catalog.pg_roles "
                "WHERE rolname='teruisi_ai_seal_writer'")
            self.assertEqual(cursor.fetchone(), (False,))
            cursor.execute("GRANT SELECT,UPDATE ON public.ai_business_v4_runs "
                "TO teruisi_ai_writer")
            cursor.execute("SELECT has_table_privilege('teruisi_ai_writer',"
                "'public.ai_business_v4_seals','INSERT'),"
                "has_function_privilege('teruisi_ai_writer',"
                "'public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)',"
                "'EXECUTE'),has_function_privilege('teruisi_ai_reader',"
                "'public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)',"
                "'EXECUTE')")
            self.assertEqual(cursor.fetchone(), (False, False, False))
        authority = AiWriteAuthority.objects.get(id=1)
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_writer")
            db.execute("BEGIN")
            db.execute("SELECT set_config('teruisi.ai_epoch',%s,true),"
                "set_config('teruisi.ai_cutover',%s,true)",
                [str(authority.authority_epoch), authority.cutover_id])
            with self.assertRaises(psycopg.Error):
                db.execute("UPDATE public.ai_business_v4_runs "
                    "SET status='sealed',version=version+1 WHERE id=%s",
                    [self.parent.id])
            db.execute("ROLLBACK")
            with self.assertRaises(psycopg.Error):
                db.execute("SELECT * FROM public.ai_v4_commit_seal(%s,%s,%s,%s,%s,%s,%s)",
                    [self.parent.id, attempt_id, body["evidenceVersion"] - 1,
                     canonical(body), digest(canonical(body)), "f" * 64, body["keyId"]])
        self.assertEqual(m.AiBusinessV4Run.objects.get(pk=self.parent.pk).status,
            "collecting")
        self.assertFalse(m.AiBusinessV4Seal.objects.filter(run_id=self.parent.id).exists())

    def test_sealer_still_rejects_wrong_source_body_and_invalid_mac_shape(self):
        attempt_id = self.attempt()
        body = self.body(attempt_id)
        altered = json.loads(canonical(body))
        altered["sources"][0]["rowCount"] += 1
        with self.assertRaisesRegex(psycopg.Error,
                "ai_business_v4_seal_source_incomplete"):
            self.call_as_sealer(altered)
        with self.assertRaisesRegex(psycopg.Error,
                "ai_business_v4_seal_identity_invalid"):
            self.call_as_sealer(body, mac="not-a-mac")
        with self.assertRaisesRegex(psycopg.Error, "ai_screen_fields_invalid"):
            self.call_as_sealer(body, raw_body=canonical(body)[:-1]
                + ',"sourceAuthorityVerified":true}')
        with self.assertRaisesRegex(psycopg.Error, "ai_screen_fields_invalid"):
            self.call_as_sealer(body, raw_body='{"schemaVersion":"duplicate",'
                + canonical(body)[1:])
        self.assertEqual(m.AiBusinessV4Run.objects.get(pk=self.parent.pk).status,
            "collecting")
        self.assertFalse(m.AiBusinessV4Seal.objects.filter(run_id=self.parent.id).exists())

    def test_mistaken_ai_writer_insert_grant_is_still_rejected_by_session_role(self):
        attempt_id = self.attempt()
        body = self.body(attempt_id)
        raw = canonical(body)
        with connection.cursor() as cursor:
            cursor.execute("GRANT INSERT ON public.ai_business_v4_seals "
                "TO teruisi_ai_writer")
        try:
            with self.database() as db:
                db.execute("SET SESSION AUTHORIZATION teruisi_ai_writer")
                db.execute("BEGIN")
                with self.assertRaisesRegex(psycopg.Error,
                        "ai_business_v4_seal_role_denied"):
                    db.execute("INSERT INTO public.ai_business_v4_seals "
                        "(run_id,attempt_id,evidence_version,body_json,body_digest,"
                        "body_mac,key_id,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,now())",
                        [body["runId"], body["attemptId"], body["evidenceVersion"],
                         raw, digest(raw), "f" * 64, body["keyId"]])
                db.execute("ROLLBACK")
            self.assertFalse(m.AiBusinessV4Seal.objects.filter(
                run_id=self.parent.id).exists())
        finally:
            with connection.cursor() as cursor:
                cursor.execute("REVOKE INSERT ON public.ai_business_v4_seals "
                    "FROM teruisi_ai_writer")

    def test_truncate_privilege_drift_and_direct_truncate_fail_closed(self):
        attempt_id = self.attempt()
        body = self.body(attempt_id)
        with connection.cursor() as cursor:
            cursor.execute("GRANT TRUNCATE ON public.finance_lines "
                "TO teruisi_finance_writer")
        try:
            with self.assertRaisesRegex(psycopg.Error,
                    "ai_v4_admission_source_truncate_privilege"):
                self.call_as_sealer(body)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("REVOKE TRUNCATE ON public.finance_lines "
                    "FROM teruisi_finance_writer")
        with connection.cursor() as cursor:
            cursor.execute("GRANT TRUNCATE ON public.ai_business_v4_seals "
                "TO teruisi_ai_seal_writer,teruisi_ai_writer")
        try:
            with self.assertRaisesRegex(psycopg.Error,
                    "ai_business_v4_commit_sealer_privileges_invalid"):
                self.call_as_sealer(body)
            with self.database() as writer:
                writer.execute("SET SESSION AUTHORIZATION teruisi_ai_writer")
                with self.assertRaisesRegex(psycopg.Error,
                        "ai_business_v4_seal_truncate_denied"):
                    writer.execute("TRUNCATE public.ai_business_v4_seals")
            self.assertEqual(m.AiBusinessV4Run.objects.get(pk=self.parent.pk).status,
                "collecting")
        finally:
            with connection.cursor() as cursor:
                cursor.execute("REVOKE TRUNCATE ON public.ai_business_v4_seals "
                    "FROM teruisi_ai_seal_writer,teruisi_ai_writer")
        # The actual isolated table owner can still run Django's bounded
        # flush/restore lifecycle; no ordinary writer inherits that identity.
        with connection.cursor() as cursor:
            cursor.execute("TRUNCATE public.ai_business_v4_seals")

    def test_cross_run_and_stale_attempt_cannot_seal(self):
        attempt_id = self.attempt()
        body = self.body(attempt_id)
        other = m.AiBusinessV4Run.objects.exclude(pk=self.parent.pk).get(
            owner_email=self.principal.email)
        wrong = json.loads(canonical(body))
        wrong["runId"] = other.id
        with self.assertRaises(psycopg.Error):
            self.call_as_sealer(wrong)
        from access_control.models import AppUser
        AppUser.objects.filter(email=self.principal.email).update(version=2)
        fresh = validation.start_attempt(self.parent.id,
            m.AiBusinessV4Run.objects.get(pk=self.parent.pk).version,
            self.principal)
        self.assertNotEqual(fresh["attemptId"], attempt_id)
        with self.assertRaises(psycopg.Error):
            self.call_as_sealer(body)
        self.assertFalse(m.AiBusinessV4Seal.objects.filter(run_id=self.parent.id).exists())

    def test_deferred_commit_failure_rolls_back_seal_parent_and_global_revision(self):
        attempt_id = self.attempt()
        body = self.body(attempt_id)
        before = AiDataRevision.objects.get(domain="ai-assistant").revision
        with connection.cursor() as cursor:
            cursor.execute("CREATE FUNCTION public.ai_v4_test_abort_commit() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic_deferred_failure'; END $$")
            cursor.execute("CREATE CONSTRAINT TRIGGER ai_v4_test_abort_commit "
                "AFTER UPDATE ON public.ai_business_v4_runs "
                "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                "EXECUTE FUNCTION public.ai_v4_test_abort_commit()")
        try:
            with self.assertRaisesRegex(psycopg.Error, "synthetic_deferred_failure"):
                self.call_as_sealer(body)
            self.assertEqual(m.AiBusinessV4Run.objects.get(pk=self.parent.pk).status,
                "collecting")
            self.assertFalse(m.AiBusinessV4Seal.objects.filter(run_id=self.parent.id).exists())
            self.assertEqual(AiDataRevision.objects.get(
                domain="ai-assistant").revision, before)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("DROP TRIGGER ai_v4_test_abort_commit "
                    "ON public.ai_business_v4_runs")
                cursor.execute("DROP FUNCTION public.ai_v4_test_abort_commit()")

"""Isolated PostgreSQL role proof for one explicit, non-spendable cap."""
from datetime import datetime, timedelta, timezone
from importlib import import_module
import hashlib
from unittest.mock import patch

from django import test as djtest
from django.db import DatabaseError, connection, transaction
from django.db.models import F

from access_control.models import AppUser
from . import business_market_v2_human_cap_contract as contract
from . import business_market_v2_human_cap_owner as owner
from . import business_market_v2_human_cap_sql as cap_sql
from . import business_market_v2_parked_creation as parked
from . import business_market_v2_material_admission as material_owner
from . import business_market_v2_admitted_paused as admitted_service
from . import business_market_v2_execution_snapshot as snapshot_service
from . import models as m
from . import test_business_market_v2_cost_admission as fixture
from .policy import AiError
from .test_business_market_v2_material_role_bridge import session_role


MIGRATION = import_module(
    "ai_assistant.migrations.0074_business_market_v2_human_cap_approval")


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class HumanCapRoleTests(djtest.TransactionTestCase):
    user = fixture.MarketV2CostAdmissionTests.user
    request_body = fixture.MarketV2CostAdmissionTests.request_body
    current_catalog = fixture.MarketV2CostAdmissionTests.current_catalog
    create_fixed_report = fixture.MarketV2CostAdmissionTests.create_fixed_report
    planned_evidence_body = fixture.MarketV2CostAdmissionTests.planned_evidence_body
    selector = fixture.MarketV2CostAdmissionTests.selector
    _attest_as_role = staticmethod(fixture.MarketV2CostAdmissionTests._attest_as_role)
    setUp = fixture.MarketV2CostAdmissionTests.setUp
    def parked_id(self):
        serial = getattr(self, "cap_serial", 0) + 1
        self.cap_serial = serial
        return parked.create({"schemaVersion": parked.REQUEST_SCHEMA,
            "clientRequestId": f"human-cap-parked-{serial}",
            "sourceReportId": self.report.id,
            "marketSelector": self.selector()}, self.admin)["item"]["id"]

    def admitted(self):
        parked_id = self.parked_id()
        prepared = material_owner.prepare_candidate(parked_id, self.admin)
        self._attest_as_role(parked_id, prepared)
        body = {"schemaVersion": admitted_service.REQUEST_SCHEMA,
            "clientRequestId": f"human-cap-admitted-{self.cap_serial}",
            "parkedReportId": parked_id}
        created = admitted_service.create(body, self.admin)
        return parked_id, created, {}, prepared

    def body(self, report_id, client=None):
        return {"schemaVersion": snapshot_service.REQUEST_SCHEMA,
            "clientRequestId": client or f"human-cap-execution-{self.cap_serial}",
            "admittedReportId": report_id}
    create_plan = fixture.MarketV2CostAdmissionTests.create_plan
    attested = fixture.MarketV2CostAdmissionTests.attested
    prepared = fixture.MarketV2CostAdmissionTests.prepared
    def plan_and_model(self):
        created, _, prepared = self.prepared()
        from . import test_business_market_v2_execution_plan as plan_fixture
        with djtest.override_settings(AI_MARKET_V2_EXECUTION_PLAN_ENABLED=True), \
                plan_fixture.plan_attestor():
            saved = plan_fixture.service.attest(created["reportId"],
                prepared["planJson"])
        model = m.AiModels.objects.create(
            id=f"human-cap-model-{self.cap_serial}", version=3,
            name="隔离费用候选", protocol="openai_compatible",
            model_type="text", model_name="synthetic-no-call",
            status="enabled", max_tokens=4096,
            max_tool_rounds=6, max_total_tool_calls=12)
        return created, saved["planId"], model
    input = fixture.MarketV2CostAdmissionTests.input

    def ledger(self):
        created, plan_id, model = self.plan_and_model()
        tariff, jobs, at = self.input(model)
        with session_role("teruisi_ai_reader"), djtest.override_settings(
                AI_MARKET_V2_COST_CANDIDATE_ENABLED=True):
            prepared = fixture.service.prepare(created["reportId"], tariff,
                jobs, 10, "b" * 64, self.admin, at_utc=at)
        with fixture.cost_attestor(), djtest.override_settings(
                AI_MARKET_V2_COST_CANDIDATE_ENABLED=True):
            saved = fixture.service.record(plan_id, prepared["candidateJson"])
        return created, saved["ledgerId"]

    @staticmethod
    def request_from(preview, cents=7):
        payload = {name: preview[name] for name in (
            "ledgerId", "planId", "reportId", "ledgerDigest",
            "planDigest", "reportSnapshotDigest", "modelConfigDigest")}
        payload.update(schemaVersion=contract.SCHEMA,
            approvedCapCents=cents, explicitApproval=True,
            expiresAtUtc=(datetime.now(timezone.utc)+timedelta(days=1))
                .replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"))
        return payload

    def test_authenticated_writer_approves_only_ceiling_then_revokes(self):
        created, ledger_id = self.ledger()
        outsider = self.user("human-cap-outside@example.invalid",
            "admin", None)
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)
        with patch("ai_assistant.provider.turn") as provider:
            with session_role("teruisi_ai_writer"), djtest.override_settings(
                    DJANGO_PROCESS_ROLE="ai_writer",
                    AI_MARKET_V2_HUMAN_CAP_ENABLED=True):
                preview = owner.preview(ledger_id, self.admin)
                self.assertEqual((preview["requiredClaimCents"],
                    preview["maximumClaimCents"]), (5, 10))
                self.assertFalse(preview["tariffAuthorityVerified"])
                payload = self.request_from(preview)
                receipt = owner.approve(ledger_id, payload, self.admin)
                self.assertEqual(receipt["approvedCapCents"], 7)
                self.assertEqual(receipt["status"], "approved_cap_only")
                self.assertEqual(owner.approve(ledger_id, payload, self.admin), receipt)
                self.assertEqual(owner.outcome(ledger_id, self.admin)["status"],
                    "approved_cap_only")
                with self.assertRaises(DatabaseError):
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT id FROM public."
                            "protected_business_market_v2_human_cap_approvals")
                with self.assertRaises(AiError):
                    owner.preview(ledger_id, outsider)
                revoked = owner.revoke(ledger_id, {"approvalId":
                    receipt["approvalId"], "reason": "人工取消本报告上限"}, self.admin)
                self.assertEqual(revoked["status"], "revoked")
                self.assertEqual(owner.outcome(ledger_id, self.admin)["status"],
                    "revoked")
                with self.assertRaises(AiError):
                    owner.approve(ledger_id, payload, self.admin)
            provider.assert_not_called()
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM public."
                "protected_business_market_v2_human_cap_approvals")
            self.assertEqual(cursor.fetchone(), (1,))
            cursor.execute("SELECT count(*) FROM public."
                "protected_business_market_v2_human_cap_revocations")
            self.assertEqual(cursor.fetchone(), (1,))
            MIGRATION.verify_catalog(cursor)

    def test_direct_dml_and_catalog_grant_drift_are_rejected(self):
        with session_role("teruisi_ai_writer"):
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("INSERT INTO public."
                        "protected_business_market_v2_human_cap_approvals(id) "
                        "VALUES(repeat('a',64))")
        with self.assertRaises(RuntimeError):
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("GRANT INSERT ON public."
                    "protected_business_market_v2_human_cap_approvals "
                    "TO teruisi_ai_writer")
                MIGRATION.verify_catalog(cursor)
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)
        with self.assertRaises(RuntimeError):
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("DROP TRIGGER ai_market_human_cap_guard ON "
                    "public.protected_business_market_v2_human_cap_approvals")
                cursor.execute("CREATE TRIGGER ai_market_human_cap_guard "
                    "BEFORE INSERT OR UPDATE OR DELETE ON "
                    "public.protected_business_market_v2_human_cap_approvals "
                    "FOR EACH ROW WHEN (false) EXECUTE FUNCTION " +
                    cap_sql.GUARD_SIG)
                MIGRATION.verify_catalog(cursor)
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)

        with self.assertRaises(RuntimeError):
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SELECT conname FROM pg_catalog.pg_constraint "
                    "WHERE conrelid='public."
                    "protected_business_market_v2_human_cap_approvals'::regclass "
                    "AND contype='u'")
                unique_name = cursor.fetchone()[0]
                cursor.execute("ALTER TABLE public."
                    "protected_business_market_v2_human_cap_approvals "
                    "DROP CONSTRAINT " + connection.ops.quote_name(unique_name))
                MIGRATION.verify_catalog(cursor)
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)

    def test_account_version_replay_does_not_reapprove_or_rerevoke(self):
        _, ledger_id = self.ledger()
        with session_role("teruisi_ai_writer"), djtest.override_settings(
                DJANGO_PROCESS_ROLE="ai_writer",
                AI_MARKET_V2_HUMAN_CAP_ENABLED=True):
            payload = self.request_from(owner.preview(ledger_id, self.admin))
            receipt = owner.approve(ledger_id, payload, self.admin)
        original = AppUser.objects.get(email=self.admin.email.lower()).version
        AppUser.objects.filter(email=self.admin.email.lower()).update(
            version=F("version") + 1)
        new_version = AppUser.objects.get(email=self.admin.email.lower()).version
        self.assertEqual(new_version, original + 1)
        encoded = contract.canonical(payload)
        with session_role("teruisi_ai_writer"):
            with self.assertRaisesRegex(DatabaseError,
                    "ai_market_human_cap_conflicting_replay"):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT public.ai_market_v2_approve_human_cap("
                        "%s,%s,%s,%s)", [ledger_id, encoded,
                        self.admin.email.lower(), new_version])
        reason = "人工撤销旧上限"
        with session_role("teruisi_ai_writer"), djtest.override_settings(
                DJANGO_PROCESS_ROLE="ai_writer",
                AI_MARKET_V2_HUMAN_CAP_ENABLED=True):
            self.assertEqual(owner.revoke(ledger_id, {"approvalId":
                receipt["approvalId"], "reason": reason}, self.admin)["status"],
                "revoked")
        with connection.cursor() as cursor:
            cursor.execute("SELECT owner_version FROM public."
                "protected_business_market_v2_human_cap_approvals "
                "WHERE id=%s", [receipt["approvalId"]])
            self.assertEqual(cursor.fetchone(), (original,))
            cursor.execute("SELECT actor_version,reason_digest FROM public."
                "protected_business_market_v2_human_cap_revocations "
                "WHERE approval_id=%s", [receipt["approvalId"]])
            recorded = cursor.fetchone()
            self.assertEqual(recorded[0], new_version)
        AppUser.objects.filter(email=self.admin.email.lower()).update(
            version=F("version") + 1)
        newer_version = AppUser.objects.get(email=self.admin.email.lower()).version
        with session_role("teruisi_ai_writer"):
            with self.assertRaisesRegex(DatabaseError,
                    "ai_market_human_cap_revoke_conflict"):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT public.ai_market_v2_revoke_human_cap("
                        "%s,%s,%s,%s)", [receipt["approvalId"],
                        self.admin.email.lower(), newer_version,
                        hashlib.sha256(reason.encode("utf-8")).hexdigest()])
        with connection.cursor() as cursor:
            cursor.execute("SELECT actor_version,reason_digest FROM public."
                "protected_business_market_v2_human_cap_revocations "
                "WHERE approval_id=%s", [receipt["approvalId"]])
            self.assertEqual(cursor.fetchone(), recorded)

    def test_same_admin_two_report_ledgers_cannot_cross_use_payload(self):
        second_base = self.create_fixed_report()
        first_report, first_ledger = self.ledger()
        with session_role("teruisi_ai_writer"), djtest.override_settings(
                DJANGO_PROCESS_ROLE="ai_writer",
                AI_MARKET_V2_HUMAN_CAP_ENABLED=True):
            first = self.request_from(owner.preview(first_ledger, self.admin))
        self.report = second_base
        second_report, second_ledger = self.ledger()
        self.assertNotEqual(first_report["reportId"], second_report["reportId"])
        self.assertNotEqual(first_ledger, second_ledger)
        with session_role("teruisi_ai_writer"), djtest.override_settings(
                DJANGO_PROCESS_ROLE="ai_writer",
                AI_MARKET_V2_HUMAN_CAP_ENABLED=True):
            second = self.request_from(owner.preview(second_ledger, self.admin))
            with self.assertRaises(AiError):
                owner.approve(second_ledger, first, self.admin)
            with self.assertRaisesRegex(DatabaseError,
                    "ai_market_human_cap_binding_changed"):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT public.ai_market_v2_approve_human_cap("
                        "%s,%s,%s,%s)", [second_ledger,
                        contract.canonical(first), self.admin.email.lower(),
                        AppUser.objects.get(email=self.admin.email.lower()).version])
            receipt = owner.approve(second_ledger, second, self.admin)
            self.assertEqual(receipt["reportId"], second_report["reportId"])
        with connection.cursor() as cursor:
            cursor.execute("SELECT ledger_id FROM public."
                "protected_business_market_v2_human_cap_approvals")
            self.assertEqual(cursor.fetchall(), [(second_ledger,)])

    def test_cap_sql_never_projects_model_credentials(self):
        for definition in (cap_sql.MODEL, cap_sql.PREVIEW, cap_sql.APPROVE):
            self.assertNotIn("api_key_encrypted", definition)
            self.assertNotIn("api_key_suffix", definition)
            self.assertNotIn("SELECT * INTO model", definition)
            self.assertNotIn("ai_market_v2_cost_candidate_expected", definition)

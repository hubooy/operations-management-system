"""Isolated PG real-role test: synthetic money is held, no provider call."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from importlib import import_module
import json
from unittest.mock import patch

from django import test as djtest
from django.db import DatabaseError, connection, transaction

from business_analysis.contracts import digest
from . import business_market_v2_paid_authority_contract as authority
from . import business_market_v2_round_reservation_contract as rounds
from . import business_market_v2_cost_admission as cost_service
from . import test_business_market_v2_cost_admission as fixture
from .policy import canonical
from .test_business_market_v2_material_role_bridge import session_role


@contextmanager
def paid_session_role(role):
    """Only the three 0069 NOLOGIN roles; preserve the old role whitelist."""
    if role not in {"teruisi_ai_market_paid_adopter",
            "teruisi_ai_market_paid_reserver",
            "teruisi_ai_market_paid_starter"}:
        raise ValueError("Only exact isolated 0069 paid rehearsal roles are supported")
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION AUTHORIZATION " + role)
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketV2PaidRoundRoleTests(djtest.TransactionTestCase):
    user = fixture.MarketV2CostAdmissionTests.user
    request_body = fixture.MarketV2CostAdmissionTests.request_body
    current_catalog = fixture.MarketV2CostAdmissionTests.current_catalog
    create_fixed_report = fixture.MarketV2CostAdmissionTests.create_fixed_report
    planned_evidence_body = fixture.MarketV2CostAdmissionTests.planned_evidence_body
    selector = fixture.MarketV2CostAdmissionTests.selector
    parked_id = fixture.MarketV2CostAdmissionTests.parked_id
    _attest_as_role = staticmethod(
        fixture.MarketV2CostAdmissionTests._attest_as_role)
    admitted = fixture.MarketV2CostAdmissionTests.admitted
    setUp = fixture.MarketV2CostAdmissionTests.setUp
    body = fixture.MarketV2CostAdmissionTests.body
    create_plan = fixture.MarketV2CostAdmissionTests.create_plan
    attested = fixture.MarketV2CostAdmissionTests.attested
    prepared = fixture.MarketV2CostAdmissionTests.prepared
    plan_and_model = fixture.MarketV2CostAdmissionTests.plan_and_model
    input = fixture.MarketV2CostAdmissionTests.input

    def test_paid_role_helper_does_not_expand_existing_reader_writer_helper(self):
        with self.assertRaises(ValueError):
            with paid_session_role("teruisi_ai_reader"):
                pass
        with self.assertRaises(ValueError):
            with session_role("teruisi_ai_market_paid_adopter"):
                pass

    def test_catalog_catches_trigger_event_fk_target_and_held_index_drift(self):
        migration = import_module(
            "ai_assistant.migrations.0069_business_market_v2_paid_round_rehearsal")
        with connection.cursor() as cursor:
            migration.verify_catalog(cursor)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("DROP TRIGGER ai_market_v2_paid_event_guard ON "
                    "public.ai_business_market_v2_round_events")
                cursor.execute("CREATE TRIGGER ai_market_v2_paid_event_guard "
                    "BEFORE INSERT ON public.ai_business_market_v2_round_events "
                    "FOR EACH ROW EXECUTE FUNCTION "
                    "public.ai_market_v2_paid_row_guard()")
                with self.assertRaisesRegex(ValueError, "triggers drift"):
                    migration.verify_catalog(cursor)
            transaction.set_rollback(True)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("DROP INDEX public.ai_market_v2_paid_held_idx")
                with self.assertRaisesRegex(ValueError, "held index drift"):
                    migration.verify_catalog(cursor)
            transaction.set_rollback(True)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT conname FROM pg_catalog.pg_constraint "
                    "WHERE conrelid='public.ai_business_market_v2_paid_authorities'::regclass "
                    "AND contype='f' AND pg_catalog.pg_get_constraintdef(oid) "
                    "LIKE 'FOREIGN KEY (plan_id)%'")
                name = cursor.fetchone()[0]
                cursor.execute("ALTER TABLE public.ai_business_market_v2_paid_authorities "
                    "DROP CONSTRAINT " + connection.ops.quote_name(name))
                cursor.execute("ALTER TABLE public.ai_business_market_v2_paid_authorities "
                    "ADD CONSTRAINT ai_market_v2_paid_wrong_plan_fk "
                    "FOREIGN KEY (plan_id) REFERENCES public."
                    "ai_business_market_v2_cost_ledger_candidates(id) "
                    "ON DELETE RESTRICT")
                with self.assertRaisesRegex(ValueError, "foreign key drift"):
                    migration.verify_catalog(cursor)
            transaction.set_rollback(True)
        with connection.cursor() as cursor:
            migration.verify_catalog(cursor)

    def prepared_authority(self):
        created, plan_id, model = self.plan_and_model()
        tariff, jobs, at = self.input(model)
        with session_role("teruisi_ai_reader"), djtest.override_settings(
                AI_MARKET_V2_COST_CANDIDATE_ENABLED=True):
            prepared = cost_service.prepare(created["reportId"], tariff,
                jobs, 5, "b" * 64, self.admin, at_utc=at)
        with fixture.cost_attestor(), djtest.override_settings(
                AI_MARKET_V2_COST_CANDIDATE_ENABLED=True):
            saved = cost_service.record(plan_id, prepared["candidateJson"])
        now = datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
        rate = {"schemaVersion": authority.SOURCE_SCHEMA,
            "providerId": tariff["providerId"], "modelId": model.id,
            "modelVersion": model.version, "sourceCurrency": "CNY",
            "inputNanoPerMillionTokens":
                tariff["inputNanoYuanPerMillionTokens"],
            "outputNanoPerMillionTokens":
                tariff["outputNanoYuanPerMillionTokens"],
            "cnyFxNumerator": 1, "cnyFxDenominator": 1,
            "rateEvidenceDigest": tariff["rateSourceDigest"],
            "fxEvidenceDigest": None, "categoriesEvidenceDigest": "c" * 64,
            "chargeCategories": list(authority.CATEGORIES),
            "effectiveAtUtc": tariff["effectiveAtUtc"],
            "expiresAtUtc": tariff["expiresAtUtc"]}
        approval = {"schemaVersion": authority.APPROVAL_SCHEMA,
            "planId": plan_id, "actorEmail": self.admin.email.lower(),
            "approvedCapCents": 5, "approvalEvidenceDigest": "b" * 64,
            "approvedAtUtc": (now - timedelta(minutes=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"),
            "expiresAtUtc": (now + timedelta(hours=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ")}
        value = authority.build(prepared["candidate"], rate, approval,
            at_utc=at)
        return created, plan_id, saved, prepared["candidate"], value

    @staticmethod
    def sql(signature, args):
        with connection.cursor() as cursor:
            cursor.execute("SELECT public." + signature, args)
            value = cursor.fetchone()[0]
            return json.loads(value) if type(value) is str and value.startswith("{") else value

    def test_atomic_slot_replay_unknown_and_closed_provider_gate(self):
        created, plan_id, saved, candidate, value = self.prepared_authority()
        with self.assertRaises(DatabaseError):
            self.sql("ai_market_v2_adopt_paid_rehearsal(%s,%s)",
                [plan_id, canonical(value)])
        with paid_session_role("teruisi_ai_market_paid_adopter"):
            authority_id = self.sql("ai_market_v2_adopt_paid_rehearsal(%s,%s)",
                [plan_id, canonical(value)])
            self.assertEqual(authority_id, self.sql(
                "ai_market_v2_adopt_paid_rehearsal(%s,%s)",
                [plan_id, canonical(value)]))
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT count(*) FROM public."
                        "ai_business_market_v2_paid_authorities")
        quote = rounds.quote_round(candidate, saved["ledgerId"],
            "commerce", 1, "e" * 64)
        with paid_session_role("teruisi_ai_market_paid_reserver"), \
                patch("ai_assistant.provider.turn") as provider:
            first = self.sql("ai_market_v2_reserve_paid_round(%s,%s,%s,%s)",
                [authority_id, "commerce", 1, "e" * 64])
            self.assertEqual(first["slotId"], quote["slotId"])
            self.assertEqual(first["intentDigest"], quote["intentDigest"])
            self.assertEqual(first["reservedCents"], 1)
            self.assertFalse(first["providerCallsAllowed"])
            self.assertEqual(first["slotId"], self.sql(
                "ai_market_v2_reserve_paid_round(%s,%s,%s,%s)",
                [authority_id, "commerce", 1, "e" * 64])["slotId"])
            for role, number, request in (("commerce", 1, "f" * 64),
                    ("commerce", 2, "e" * 64)):
                with self.subTest(role=role, number=number), \
                        self.assertRaises(DatabaseError):
                    self.sql("ai_market_v2_reserve_paid_round(%s,%s,%s,%s)",
                        [authority_id, role, number, request])
            for role in ("promotion", "market_b2b", "independent_review",
                         "report"):
                held = self.sql("ai_market_v2_reserve_paid_round(%s,%s,%s,%s)",
                    [authority_id, role, 1, "e" * 64])
                self.assertEqual(held["reservedCents"], 1)
                self.assertFalse(held["providerCallsAllowed"])
            provider.assert_not_called()
        with paid_session_role("teruisi_ai_market_paid_starter"), \
                patch("ai_assistant.provider.turn") as provider:
            started = self.sql("ai_market_v2_start_paid_dispatch(%s,%s)",
                [quote["slotId"], quote["intentDigest"]])
            self.assertEqual(started["phase"], "dispatch_outcome_unknown")
            self.assertFalse(started["providerCallsAllowed"])
            with self.assertRaisesRegex(DatabaseError,
                    "ai_market_v2_paid_dispatch_unknown_no_retry"):
                self.sql("ai_market_v2_start_paid_dispatch(%s,%s)",
                    [quote["slotId"], quote["intentDigest"]])
            provider.assert_not_called()
        with connection.cursor() as cursor:
            cursor.execute("SELECT reserved_cents FROM public."
                "ai_business_market_v2_cost_ledger_candidates WHERE id=%s",
                [saved["ledgerId"]])
            self.assertEqual(cursor.fetchone(), (0,))
            cursor.execute("SELECT count(*) FROM public."
                "ai_business_market_v2_round_reservations")
            self.assertEqual(cursor.fetchone(), (5,))
            cursor.execute("SELECT sum(max_cost_cents) FROM public."
                "ai_business_market_v2_round_reservations")
            self.assertEqual(cursor.fetchone(), (5,))
            cursor.execute("SELECT count(*) FROM public."
                "ai_business_market_v2_round_events")
            self.assertEqual(cursor.fetchone(), (1,))
        for statement in (
                "UPDATE public.ai_business_market_v2_paid_authorities "
                    "SET status='synthetic_rehearsal_only'",
                "DELETE FROM public.ai_business_market_v2_round_reservations"):
            with self.subTest(statement=statement), self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute(statement)
        # The trusted table owner may TRUNCATE for TransactionTestCase flush;
        # the paid runtime identity must have neither a grant nor a bypass.
        with paid_session_role("teruisi_ai_market_paid_starter"):
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("TRUNCATE public.ai_business_market_v2_round_events")
        self.assertFalse(value["providerCallsAllowed"])

    def test_forged_authority_and_ordinary_role_are_denied(self):
        _, plan_id, _, _, value = self.prepared_authority()
        forged = deepcopy(value)
        forged["source"]["chargeCategories"].append("tool_calls")
        forged["sourceDigest"] = digest(forged["source"])
        forged["authorityDigest"] = digest({key: item for key, item in
            forged.items() if key != "authorityDigest"})
        with paid_session_role("teruisi_ai_market_paid_adopter"):
            with self.assertRaises(DatabaseError):
                self.sql("ai_market_v2_adopt_paid_rehearsal(%s,%s)",
                    [plan_id, canonical(forged)])
        with session_role("teruisi_ai_writer"):
            with self.assertRaises(DatabaseError):
                self.sql("ai_market_v2_adopt_paid_rehearsal(%s,%s)",
                    [plan_id, canonical(value)])
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT id FROM public."
                        "ai_business_market_v2_round_reservations")

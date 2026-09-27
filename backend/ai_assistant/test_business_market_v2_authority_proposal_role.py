"""Isolated PG: separate pending rate and cap roles never authorize a call."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from importlib import import_module
import json
from unittest.mock import patch

from django import test as djtest
from django.db import DatabaseError, connection, transaction

from access_control.models import AppUser
from business_analysis.contracts import digest
from . import business_market_v2_authority_adoption_contract as adoption
from . import models as m
from . import test_business_market_v2_paid_round_role as fixture
from .policy import canonical
from .test_business_market_v2_material_role_bridge import session_role


ROLES = {"teruisi_ai_market_rate_proposer",
    "teruisi_ai_market_cap_proposer",
    "teruisi_ai_market_proposal_revoker"}


@contextmanager
def authority_proposal_role(role):
    if role not in ROLES:
        raise ValueError("Only exact 0072 authority proposal roles are supported")
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION AUTHORIZATION " + role)
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketV2AuthorityProposalRoleTests(djtest.TransactionTestCase):
    user = fixture.MarketV2PaidRoundRoleTests.user
    request_body = fixture.MarketV2PaidRoundRoleTests.request_body
    current_catalog = fixture.MarketV2PaidRoundRoleTests.current_catalog
    create_fixed_report = fixture.MarketV2PaidRoundRoleTests.create_fixed_report
    planned_evidence_body = fixture.MarketV2PaidRoundRoleTests.planned_evidence_body
    selector = fixture.MarketV2PaidRoundRoleTests.selector
    parked_id = fixture.MarketV2PaidRoundRoleTests.parked_id
    _attest_as_role = staticmethod(
        fixture.MarketV2PaidRoundRoleTests._attest_as_role)
    admitted = fixture.MarketV2PaidRoundRoleTests.admitted
    setUp = fixture.MarketV2PaidRoundRoleTests.setUp
    body = fixture.MarketV2PaidRoundRoleTests.body
    create_plan = fixture.MarketV2PaidRoundRoleTests.create_plan
    attested = fixture.MarketV2PaidRoundRoleTests.attested
    prepared = fixture.MarketV2PaidRoundRoleTests.prepared
    plan_and_model = fixture.MarketV2PaidRoundRoleTests.plan_and_model
    input = fixture.MarketV2PaidRoundRoleTests.input
    prepared_authority = fixture.MarketV2PaidRoundRoleTests.prepared_authority

    @staticmethod
    def call(name, args):
        with connection.cursor() as cursor:
            cursor.execute("SELECT public." + name, args)
            value = cursor.fetchone()[0]
            return json.loads(value) if type(value) is str and value.startswith("{") else value

    def proposals(self):
        created, plan_id, saved, candidate, rehearsal = self.prepared_authority()
        actor = AppUser.objects.get(email=self.admin.email.lower())
        plan = {"planId": plan_id, "executionRoot": {
            "executionReportId": created["reportId"],
            "ownerEmail": self.admin.email.lower()}}
        value = adoption.build(plan, candidate, saved["ledgerId"],
            rehearsal["source"], rehearsal["approval"], actor.version,
            at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        return plan_id, candidate, value

    def test_pending_roles_revocation_and_reader_never_grant(self):
        plan_id, _, value = self.proposals()
        with patch("ai_assistant.provider.turn") as provider:
            with self.assertRaises(DatabaseError):
                self.call("ai_market_v2_record_rate_proposal(%s,%s)",
                    [plan_id, canonical(value["rate"])])
            with authority_proposal_role("teruisi_ai_market_rate_proposer"):
                rate_id = self.call("ai_market_v2_record_rate_proposal(%s,%s)",
                    [plan_id, canonical(value["rate"])])
                self.assertEqual(rate_id, self.call(
                    "ai_market_v2_record_rate_proposal(%s,%s)",
                    [plan_id, canonical(value["rate"])]))
                with self.assertRaises(DatabaseError):
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT id FROM public."
                            "protected_business_market_v2_rate_proposals")
            with authority_proposal_role("teruisi_ai_market_cap_proposer"):
                with self.assertRaises(DatabaseError):
                    self.call("ai_market_v2_record_rate_proposal(%s,%s)",
                        [plan_id, canonical(value["rate"])])
                cap_id = self.call("ai_market_v2_record_cap_proposal(%s,%s)",
                    [rate_id, canonical(value["cap"])])
                self.assertEqual(cap_id, self.call(
                    "ai_market_v2_record_cap_proposal(%s,%s)",
                    [rate_id, canonical(value["cap"])]))
            with session_role("teruisi_ai_reader"):
                receipt = self.call(
                    "ai_market_v2_authority_proposal_receipt(%s,%s,%s)",
                    [plan_id, self.admin.email.lower(),
                     value["rate"]["ownerVersion"]])
                self.assertEqual(receipt["rateProposalId"], rate_id)
                self.assertEqual(receipt["capProposalId"], cap_id)
                self.assertFalse(receipt["tariffAuthorityVerified"])
                self.assertFalse(receipt["humanApprovalVerified"])
                self.assertFalse(receipt["providerCallsAllowed"])
            with authority_proposal_role("teruisi_ai_market_proposal_revoker"):
                revoke_id = self.call(
                    "ai_market_v2_revoke_authority_proposal(%s,%s,%s)",
                    ["rate", rate_id, "f" * 64])
                self.assertEqual(revoke_id, self.call(
                    "ai_market_v2_revoke_authority_proposal(%s,%s,%s)",
                    ["rate", rate_id, "f" * 64]))
                with self.assertRaisesRegex(DatabaseError,
                        "ai_market_v2_revocation_conflicting_replay"):
                    self.call("ai_market_v2_revoke_authority_proposal(%s,%s,%s)",
                        ["rate", rate_id, "e" * 64])
            with session_role("teruisi_ai_reader"):
                receipt = self.call(
                    "ai_market_v2_authority_proposal_receipt(%s,%s,%s)",
                    [plan_id, self.admin.email.lower(),
                     value["rate"]["ownerVersion"]])
                self.assertTrue(receipt["rateRevoked"])
                self.assertFalse(receipt["providerCallsAllowed"])
            with authority_proposal_role("teruisi_ai_market_cap_proposer"):
                with self.assertRaisesRegex(DatabaseError,
                        "ai_market_v2_cap_proposal_root_invalid"):
                    self.call("ai_market_v2_record_cap_proposal(%s,%s)",
                        [rate_id, canonical(value["cap"])])
            provider.assert_not_called()

    def test_rehashed_unknown_charge_category_cannot_enter_pending_rate(self):
        plan_id, _, value = self.proposals()
        forged = deepcopy(value["rate"])
        forged["source"]["chargeCategories"].append("tool_calls")
        forged["chargeCategories"].append("tool_calls")
        forged["sourceDigest"] = digest(forged["source"])
        forged["rateProposalDigest"] = digest({key: item for key, item in
            forged.items() if key != "rateProposalDigest"})
        with authority_proposal_role("teruisi_ai_market_rate_proposer"):
            with self.assertRaisesRegex(DatabaseError,
                    "ai_market_v2_rate_source_invalid"):
                self.call("ai_market_v2_record_rate_proposal(%s,%s)",
                    [plan_id, canonical(forged)])
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM public."
                "protected_business_market_v2_rate_proposals")
            self.assertEqual(cursor.fetchone(), (0,))

    def test_current_model_settings_drift_refuses_source_proposal(self):
        plan_id, _, value = self.proposals()
        model = m.AiModels.objects.get(pk=value["rate"]["modelId"])
        model.max_tokens += 1
        model.save(update_fields=["max_tokens"])
        with authority_proposal_role("teruisi_ai_market_rate_proposer"):
            with self.assertRaisesRegex(DatabaseError,
                    "ai_market_v2_rate_proposal_root_invalid"):
                self.call("ai_market_v2_record_rate_proposal(%s,%s)",
                    [plan_id, canonical(value["rate"])])

    def test_role_lists_are_separate_and_default_tables_empty(self):
        with self.assertRaises(ValueError):
            with authority_proposal_role("teruisi_ai_market_paid_adopter"):
                pass
        with self.assertRaises(ValueError):
            with session_role("teruisi_ai_market_rate_proposer"):
                pass
        with connection.cursor() as cursor:
            for table in ("protected_business_market_v2_rate_proposals",
                    "protected_business_market_v2_cap_proposals",
                    "protected_business_market_v2_authority_revocations"):
                cursor.execute("SELECT count(*) FROM public." + table)
                self.assertEqual(cursor.fetchone(), (0,))

    def test_catalog_detects_trigger_event_and_fk_target_drift(self):
        migration = import_module(
            "ai_assistant.migrations.0072_business_market_v2_authority_proposals")
        with connection.cursor() as cursor:
            migration.verify_catalog(cursor)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("DROP TRIGGER ai_market_v2_authority_rate_guard "
                    "ON public.protected_business_market_v2_rate_proposals")
                cursor.execute("CREATE TRIGGER ai_market_v2_authority_rate_guard "
                    "BEFORE INSERT ON public.protected_business_market_v2_rate_proposals "
                    "FOR EACH ROW EXECUTE FUNCTION "
                    "public.ai_market_v2_authority_proposal_guard()")
                with self.assertRaisesRegex(ValueError, "trigger drift"):
                    migration.verify_catalog(cursor)
            transaction.set_rollback(True)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT conname FROM pg_catalog.pg_constraint "
                    "WHERE conrelid='public.protected_business_market_v2_rate_proposals'::regclass "
                    "AND contype='f' AND pg_catalog.pg_get_constraintdef(oid) "
                    "LIKE 'FOREIGN KEY (plan_id)%'")
                name = cursor.fetchone()[0]
                cursor.execute("ALTER TABLE public."
                    "protected_business_market_v2_rate_proposals DROP CONSTRAINT "
                    + connection.ops.quote_name(name))
                cursor.execute("ALTER TABLE public."
                    "protected_business_market_v2_rate_proposals ADD CONSTRAINT "
                    "ai_market_v2_wrong_rate_plan_fk FOREIGN KEY (plan_id) "
                    "REFERENCES public.ai_business_market_v2_cost_ledger_candidates(id) "
                    "ON DELETE RESTRICT")
                with self.assertRaisesRegex(ValueError, "FK target drift"):
                    migration.verify_catalog(cursor)
            transaction.set_rollback(True)
        with connection.cursor() as cursor:
            migration.verify_catalog(cursor)

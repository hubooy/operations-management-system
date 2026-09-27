"""Real-role PG: pending CNY requirement, zero funds and no model call."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django import test as djtest
from django.db import DatabaseError, connection

from access_control.models import AppUser
from . import business_market_v2_cost_admission as service
from . import models as m
from . import test_business_market_v2_execution_plan as fixture
from .policy import AiError, canonical, digest
from .test_business_market_v2_material_role_bridge import session_role


@contextmanager
def cost_attestor():
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION AUTHORIZATION teruisi_ai_market_cost_attestor")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketV2CostAdmissionTests(djtest.TransactionTestCase):
    user = fixture.MarketV2ExecutionPlanTests.user
    request_body = fixture.MarketV2ExecutionPlanTests.request_body
    current_catalog = fixture.MarketV2ExecutionPlanTests.current_catalog
    create_fixed_report = fixture.MarketV2ExecutionPlanTests.create_fixed_report
    planned_evidence_body = fixture.MarketV2ExecutionPlanTests.planned_evidence_body
    selector = fixture.MarketV2ExecutionPlanTests.selector
    parked_id = fixture.MarketV2ExecutionPlanTests.parked_id
    _attest_as_role = staticmethod(fixture.MarketV2ExecutionPlanTests._attest_as_role)
    admitted = fixture.MarketV2ExecutionPlanTests.admitted
    setUp = fixture.MarketV2ExecutionPlanTests.setUp
    body = fixture.MarketV2ExecutionPlanTests.body
    create_plan = fixture.MarketV2ExecutionPlanTests.create_plan
    attested = fixture.MarketV2ExecutionPlanTests.attested
    prepared = fixture.MarketV2ExecutionPlanTests.prepared

    def plan_and_model(self):
        created, _, prepared = self.prepared()
        with djtest.override_settings(AI_MARKET_V2_EXECUTION_PLAN_ENABLED=True), \
                fixture.plan_attestor():
            saved=fixture.service.attest(created["reportId"],prepared["planJson"])
        model=m.AiModels.objects.create(id="cost-test-model",version=3,
            name="隔离费用候选",protocol="openai_compatible",model_type="text",
            model_name="synthetic-no-call",status="enabled",
            max_tokens=4096,max_tool_rounds=6,max_total_tool_calls=12)
        return created,saved["planId"],model

    def input(self,model):
        now=datetime.now(timezone.utc).replace(microsecond=0)
        tariff={"schemaVersion":"business-model-cny-tariff-candidate-v1",
            "providerId":"unverified-provider","modelId":model.id,
            "modelVersion":model.version,"currency":"CNY",
            "inputNanoYuanPerMillionTokens":1_000_000_000,
            "outputNanoYuanPerMillionTokens":2_000_000_000,
            "rateSourceDigest":"a"*64,
            "effectiveAtUtc":(now-timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expiresAtUtc":(now+timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")}
        roles=("commerce","promotion","market_b2b","independent_review","report")
        jobs=[{"role":role,"maxRounds":1,"maxInputTokensPerRound":1000,
            "maxOutputTokensPerRound":1000} for role in roles]
        return tariff,jobs,now.strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_real_roles_record_only_required_zero_reserved(self):
        created,plan_id,model=self.plan_and_model()
        tariff,jobs,at=self.input(model)
        with self.assertRaises(AiError):
            service.prepare(created["reportId"],tariff,jobs,5,"b"*64,self.admin,
                at_utc=at)
        with session_role("teruisi_ai_reader"), \
                djtest.override_settings(AI_MARKET_V2_COST_CANDIDATE_ENABLED=True):
            prepared=service.prepare(created["reportId"],tariff,jobs,5,"b"*64,
                self.admin,at_utc=at)
        candidate=prepared["candidate"]
        self.assertEqual(candidate["requiredCents"],5)
        self.assertEqual(candidate["reservedCents"],0)
        self.assertFalse(candidate["providerCallsAllowed"])
        with self.assertRaises(AiError):
            service.record(plan_id,prepared["candidateJson"])
        with cost_attestor(), \
                djtest.override_settings(AI_MARKET_V2_COST_CANDIDATE_ENABLED=True), \
                patch("ai_assistant.provider.turn") as provider:
            saved=service.record(plan_id,prepared["candidateJson"])
            self.assertEqual(saved,service.record(plan_id,prepared["candidateJson"]))
            provider.assert_not_called()
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT id FROM public."
                        "ai_business_market_v2_cost_ledger_candidates LIMIT 1")
        row=m.AiBusinessMarketV2CostLedgerCandidate.objects.get(pk=saved["ledgerId"])
        self.assertEqual((row.required_cents,row.cap_claim_cents,row.reserved_cents),
            (5,5,0))
        self.assertEqual(row.status,"pending_rate_and_approval_verification")
        self.assertEqual(row.candidate_digest,digest(row.candidate_json))
        with session_role("teruisi_ai_reader"):
            receipt=service.read(plan_id,self.admin)
            self.assertEqual(receipt["ledgerReservedCents"],0)
            self.assertFalse(receipt["providerCallsAllowed"])
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT required_cents FROM public."
                        "ai_business_market_v2_cost_ledger_candidates LIMIT 1")
        outside=self.user("market-cost-outside@example.invalid","admin",None)
        outside_version=AppUser.objects.get(email=outside.email.lower()).version
        with session_role("teruisi_ai_reader"):
            with self.assertRaisesRegex(DatabaseError,
                    "ai_market_v2_cost_receipt_mismatch"):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT public.ai_market_v2_cost_candidate_receipt(%s,%s,%s)",
                        [plan_id,outside.email.lower(),outside_version])
        with session_role("teruisi_ai_writer"):
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT required_cents FROM public."
                        "ai_business_market_v2_cost_ledger_candidates LIMIT 1")
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT public.ai_market_v2_record_cost_candidate(%s,%s)",
                        [plan_id,prepared["candidateJson"]])
        self.assertFalse(m.AiAgentJobs.objects.filter(
            workflow_run_id=created["workflowId"]).exists())

    def test_wrong_currency_claimed_permission_extra_fee_and_account_reject(self):
        created,plan_id,model=self.plan_and_model()
        tariff,jobs,at=self.input(model)
        with session_role("teruisi_ai_reader"), \
                djtest.override_settings(AI_MARKET_V2_COST_CANDIDATE_ENABLED=True):
            prepared=service.prepare(created["reportId"],tariff,jobs,5,"b"*64,
                self.admin,at_utc=at)
            wrong=deepcopy(tariff);wrong["currency"]="USD"
            with self.assertRaises(Exception):
                service.prepare(created["reportId"],wrong,jobs,5,"b"*64,
                    self.admin,at_utc=at)
            wrong=deepcopy(tariff);wrong["extraFeeNanoYuan"]=1
            with self.assertRaises(Exception):
                service.prepare(created["reportId"],wrong,jobs,5,"b"*64,
                    self.admin,at_utc=at)
        forged=deepcopy(prepared["candidate"])
        forged["providerCallsAllowed"]=True
        forged["candidateDigest"]=digest({k:v for k,v in forged.items()
            if k!="candidateDigest"})
        with cost_attestor(), \
                djtest.override_settings(AI_MARKET_V2_COST_CANDIDATE_ENABLED=True):
            with self.assertRaisesRegex(DatabaseError,
                    "ai_market_v2_cost_model_or_authority_invalid"):
                service.record(plan_id,canonical(forged))
        self.assertEqual(m.AiBusinessMarketV2CostLedgerCandidate.objects.count(),0)

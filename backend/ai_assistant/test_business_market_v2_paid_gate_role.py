"""Isolated PG: persisted synthetic jobs cannot cross the paid-call gate."""
from unittest.mock import patch

from django import test as djtest

from . import business_market_v2_active_synthetic as synthetic
from . import business_market_v2_paid_gate as gate
from . import models as m
from . import test_business_market_v2_active_synthetic as fixture
from .policy import AiError


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketV2PaidGateRoleTests(djtest.TransactionTestCase):
    user = fixture.MarketV2SyntheticChainTests.user
    request_body = fixture.MarketV2SyntheticChainTests.request_body
    current_catalog = fixture.MarketV2SyntheticChainTests.current_catalog
    create_fixed_report = fixture.MarketV2SyntheticChainTests.create_fixed_report
    planned_evidence_body = fixture.MarketV2SyntheticChainTests.planned_evidence_body
    selector = fixture.MarketV2SyntheticChainTests.selector
    parked_id = fixture.MarketV2SyntheticChainTests.parked_id
    _attest_as_role = staticmethod(
        fixture.MarketV2SyntheticChainTests._attest_as_role)
    admitted = fixture.MarketV2SyntheticChainTests.admitted
    setUp = fixture.MarketV2SyntheticChainTests.setUp
    body = fixture.MarketV2SyntheticChainTests.body
    create_plan = fixture.MarketV2SyntheticChainTests.create_plan
    attested = fixture.MarketV2SyntheticChainTests.attested
    prepared = fixture.MarketV2SyntheticChainTests.prepared
    source_plan = fixture.MarketV2SyntheticChainTests.source_plan

    def test_all_five_persisted_jobs_refuse_per_round_reservation(self):
        _, plan_id = self.source_plan()
        with djtest.override_settings(AI_MARKET_V2_SYNTHETIC_ENABLED=True), \
                fixture.synthetic_attestor():
            created = synthetic.create(plan_id)
        self.assertEqual(len(created["jobIds"]), 5)
        with djtest.override_settings(AI_MARKET_V2_PAID_RUNTIME_ENABLED=True), \
                patch("ai_assistant.provider.turn") as provider:
            for role, job_id in created["jobIds"].items():
                with self.subTest(role=role):
                    job = m.AiAgentJobs.objects.get(pk=job_id)
                    self.assertTrue(gate.is_market_job(job))
                    with self.assertRaises(AiError) as failure:
                        gate.before_reservation(job)
                    self.assertEqual(failure.exception.code,
                        "market_v2_paid_reservation_unavailable")
            provider.assert_not_called()
        self.assertEqual(m.AiAgentProviderDispatches.objects.filter(
            job_id__in=created["jobIds"].values()).count(), 5)

"""Owned candidate stays closed without a real 0062 receipt on all five jobs."""
from unittest.mock import patch

from django import test as djtest

from . import business_market_v2_owned_result as service
from . import test_business_market_v2_active_synthetic as synthetic
from . import test_business_market_v2_result_candidate as candidate_fixture
from . import models as m
from .policy import AiError
from .test_business_market_v2_material_role_bridge import session_role


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketV2OwnedResultRoleTests(djtest.TransactionTestCase):
    """Run against isolated PG with actual reader and synthetic-attestor roles."""

    user = synthetic.MarketV2SyntheticChainTests.user
    request_body = synthetic.MarketV2SyntheticChainTests.request_body
    current_catalog = synthetic.MarketV2SyntheticChainTests.current_catalog
    create_fixed_report = synthetic.MarketV2SyntheticChainTests.create_fixed_report
    planned_evidence_body = synthetic.MarketV2SyntheticChainTests.planned_evidence_body
    selector = synthetic.MarketV2SyntheticChainTests.selector
    parked_id = synthetic.MarketV2SyntheticChainTests.parked_id
    _attest_as_role = staticmethod(
        synthetic.MarketV2SyntheticChainTests._attest_as_role)
    admitted = synthetic.MarketV2SyntheticChainTests.admitted
    setUp = synthetic.MarketV2SyntheticChainTests.setUp
    body = synthetic.MarketV2SyntheticChainTests.body
    create_plan = synthetic.MarketV2SyntheticChainTests.create_plan
    attested = synthetic.MarketV2SyntheticChainTests.attested
    prepared = synthetic.MarketV2SyntheticChainTests.prepared
    source_plan = synthetic.MarketV2SyntheticChainTests.source_plan

    def test_real_reader_cannot_promote_synthetic_or_empty_execution(self):
        original, plan_id = self.source_plan()
        with djtest.override_settings(AI_MARKET_V2_SYNTHETIC_ENABLED=True), \
                synthetic.synthetic_attestor():
            artificial = synthetic.service.create(plan_id)
        with session_role("teruisi_ai_reader"):
            with self.assertRaises(AiError):
                service.read(original["reportId"], self.admin)
            with djtest.override_settings(AI_MARKET_V2_OWNED_RESULT_ENABLED=True):
                with self.assertRaises(AiError):
                    service.read(original["reportId"], self.admin)
                with self.assertRaises(AiError):
                    service.read(artificial["reportId"], self.admin)
        self.assertEqual(m.AiBusinessMarketV2ReadReceipt.objects.count(), 0)


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketV2OwnedResultOrchestrationTests(djtest.SimpleTestCase):
    def test_only_an_unchanged_owned_load_can_return_closed_candidate(self):
        root, results = candidate_fixture.fixture()
        with djtest.override_settings(AI_MARKET_V2_OWNED_RESULT_ENABLED=True), \
                patch.object(service, "_load", side_effect=[
                    (root, results, "same-fence"),
                    (root, results, "same-fence")]):
            value = service.read(root["executionReportId"], object())
        self.assertTrue(value["candidateOnly"])
        self.assertFalse(value["agentReadAuthority"])
        self.assertFalse(value["providerCallsAllowed"])
        self.assertFalse(value["owningRowsIndependentlyReplayed"])
        with djtest.override_settings(AI_MARKET_V2_OWNED_RESULT_ENABLED=True), \
                patch.object(service, "_load", side_effect=[
                    (root, results, "before"),
                    (root, results, "after")]):
            with self.assertRaises(AiError):
                service.read(root["executionReportId"], object())

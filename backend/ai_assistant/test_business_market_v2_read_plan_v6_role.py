"""Isolated PG: a plan without 0065 zero-reserve receipt cannot open v6."""
from django import test as djtest

from . import business_market_v2_read_plan_v6 as service
from . import test_business_market_v2_active_synthetic as synthetic
from . import models as m
from .policy import AiError
from .test_business_market_v2_material_role_bridge import session_role


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketV2ReadPlanV6RoleTests(djtest.TransactionTestCase):
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

    def test_default_off_and_missing_cost_never_create_new_report(self):
        original, _ = self.source_plan()
        new_report = "market-v6-new-report-negative"
        new_flow = "market-v6-new-flow-negative"
        before = m.AiReportRun.objects.count()
        with session_role("teruisi_ai_reader"):
            with self.assertRaisesRegex(AiError, "未启用"):
                service.prepare(original["reportId"], new_report, new_flow,
                    self.admin)
            with djtest.override_settings(AI_MARKET_V2_READ_PLAN_V6_ENABLED=True):
                with self.assertRaises(AiError):
                    service.prepare(original["reportId"], new_report,
                        new_flow, self.admin)
        self.assertEqual(m.AiReportRun.objects.count(), before)
        self.assertFalse(m.AiReportRun.objects.filter(pk=new_report).exists())
        self.assertFalse(m.AiWorkflowRuns.objects.filter(pk=new_flow).exists())
        self.assertEqual(m.AiBusinessMarketV2ReadReceipt.objects.count(), 0)

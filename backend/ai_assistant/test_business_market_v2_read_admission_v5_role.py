"""Isolated PG ownership check for a deliberately closed v5 admission."""
from django import test as djtest

from . import business_market_v2_read_admission_v5 as admission
from . import test_business_market_v2_active_synthetic as synthetic
from . import models as m
from .policy import AiError
from .test_business_market_v2_material_role_bridge import session_role


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketV2ReadAdmissionV5RoleTests(djtest.TransactionTestCase):
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

    def test_old_and_synthetic_reports_only_expose_closed_gaps(self):
        original, plan_id = self.source_plan()
        with djtest.override_settings(AI_MARKET_V2_SYNTHETIC_ENABLED=True), \
                synthetic.synthetic_attestor():
            artificial = synthetic.service.create(plan_id)
        with session_role("teruisi_ai_reader"):
            with self.assertRaisesRegex(AiError,
                    "市场新版本已读准入诊断未启用"):
                admission.inspect(original["reportId"],
                    artificial["reportId"], self.admin)
            with djtest.override_settings(AI_MARKET_V2_READ_ADMISSION_V5_ENABLED=True):
                old = admission.inspect(original["reportId"],
                    original["reportId"], self.admin)
                # The reader has no generic provider-dispatch table SELECT;
                # do not widen that ACL merely to inspect a synthetic chain.
                with self.assertRaisesRegex(AiError, "市场同报告已读准入证据不足"):
                    admission.inspect(original["reportId"],
                        artificial["reportId"], self.admin)
                with self.assertRaises(AiError):
                    admission.inspect(original["reportId"],
                        "another-report", self.admin)
        with djtest.override_settings(AI_MARKET_V2_READ_ADMISSION_V5_ENABLED=True):
            # The admin owns generic rows but cannot impersonate the narrow
            # 0063 reader. There is no existing identity with both surfaces.
            with self.assertRaisesRegex(AiError, "市场同报告已读准入证据不足"):
                admission.inspect(original["reportId"],
                    artificial["reportId"], self.admin)
        self.assertEqual(old["observedKind"], "parked_execution_v2")
        self.assertFalse(old["externalProviderCalled"])
        self.assertFalse(old["agentReadPersisted"])
        self.assertFalse(old["providerCallsAllowed"])
        self.assertFalse(old["numericCitationAllowed"])
        self.assertEqual(m.AiBusinessMarketV2ReadReceipt.objects.count(), 0)

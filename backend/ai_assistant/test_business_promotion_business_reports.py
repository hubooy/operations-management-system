"""New profile is recognized only for a real bound running Agent."""
import json
from unittest.mock import patch

from django import test as djtest

from . import business_reports as service
from . import business_promotion_runtime as runtime
from . import business_promotion_runtime_contract as contract
from . import test_business_promotion_agent_tool as fixtures
from .policy import AiError


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionBusinessReportRoutingTests(djtest.TransactionTestCase):
    user = fixtures.PromotionAgentToolTests.user
    call = fixtures.PromotionAgentToolTests.call
    collect_body = fixtures.PromotionAgentToolTests.collect_body
    bundle = fixtures.PromotionAgentToolTests.bundle
    input_for = fixtures.PromotionAgentToolTests.input_for
    insert = fixtures.PromotionAgentToolTests.insert
    seed = fixtures.PromotionAgentToolTests.seed
    setUp = fixtures.PromotionAgentToolTests.setUp
    request_body = fixtures.PromotionAgentToolTests.request_body
    current_catalog = fixtures.PromotionAgentToolTests.current_catalog
    create_report = fixtures.PromotionAgentToolTests.create_report
    actual_job = fixtures.PromotionAgentToolTests.actual_job

    def test_exact_new_surface_and_tools_require_real_published_job(self):
        report = self.create_report()
        job = self.actual_job(report)
        snapshot = json.loads(report.snapshot_json)
        self.assertEqual(service.required_tools(snapshot), contract.TOOLS)
        self.assertEqual(service.context(job), snapshot)
        with patch.object(runtime.transport, "catalog", side_effect=self.current_catalog):
            self.assertEqual(service.execution_surface(job, self.admin), contract.SURFACE)
        self.assertEqual({item["name"] for item in service.restricted_entries(job,
            self.current_catalog())}, contract.TOOLS)

    def test_unpublished_new_report_does_not_get_model_surface(self):
        report = self.create_report(publish=False)
        job = self.actual_job(report)
        with (patch.object(runtime.transport, "catalog", side_effect=self.current_catalog),
              self.assertRaises(AiError)):
            service.execution_surface(job, self.admin)

"""Five-tool new-surface aliases reuse owning v1 material, never Agent reads."""
import json
from unittest.mock import patch

from django import test as djtest

from . import business_market_v2_base_tool_candidate as service
from . import business_market_v2_parked_creation as parked
from . import business_market_v2_material_admission as market_material
from . import business_market_v2_admitted_paused as admitted
from . import business_promotion_creation as promotion_creation
from . import models as m
from . import test_business_market_v2_material_role_bridge as fixture
from .policy import AiError, digest, uid


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketV2BaseToolCandidateTests(djtest.TransactionTestCase):
    user = fixture.MarketV2MaterialRoleBridgeTests.user
    request_body = fixture.MarketV2MaterialRoleBridgeTests.request_body
    current_catalog = fixture.MarketV2MaterialRoleBridgeTests.current_catalog
    create_fixed_report = fixture.MarketV2MaterialRoleBridgeTests.create_fixed_report
    planned_evidence_body = fixture.MarketV2MaterialRoleBridgeTests.planned_evidence_body
    setUp = fixture.MarketV2MaterialRoleBridgeTests.setUp
    selector = fixture.MarketV2MaterialRoleBridgeTests.selector
    parked_id = fixture.MarketV2MaterialRoleBridgeTests.parked_id
    _attest_as_role = staticmethod(fixture.MarketV2MaterialRoleBridgeTests._attest_as_role)
    admitted = fixture.MarketV2MaterialRoleBridgeTests.admitted

    def roots(self):
        _, created, _, _ = self.admitted()
        source = json.loads(self.report.snapshot_json)
        return created["reportId"], source

    def test_four_new_surface_aliases_reuse_same_source_without_agent_rows(self):
        report_id, source = self.roots()
        self.assertTrue(m.AiBusinessScreeningRun.objects.filter(
            report_id=self.report.id).exists(),
            "正例必须由正式store.publish产生筛查包")
        common = {"reportId": report_id, "runId": source["evidenceRunId"],
            "screeningId": source["screeningIntent"]["id"]}
        before = (m.AiAgentJobs.objects.count(),
            m.AiAgentProviderDispatches.objects.count(),
            m.AiAgentToolDispatches.objects.count(),
            m.AiAgentToolResults.objects.count())
        with fixture.session_role("teruisi_ai_reader"), patch(
                "ai_assistant.transport.execute_tool") as remote:
            package = service.read(service.ORDER[0], {**common,
                "role": "market_b2b", "offset": 0}, self.admin)
            analysis = service.read(service.ORDER[1], {**common,
                "role": "commerce", "mode": "native", "dimension": "keyword",
                "sourceKey": source["promotionSelector"]["sourceKey"],
                "offset": 0}, self.admin)
            budget = service.read(service.ORDER[2], {**common,
                "role": "promotion", "offset": 0}, self.admin)
            keyword = service.read(service.ORDER[3], {"reportId": report_id,
                "role": "promotion", "sourceKey": source["promotionSelector"]["sourceKey"],
                "view": "keyword_sku", "offset": 0, "limit": 20}, self.admin)
            remote.assert_not_called()
        self.assertEqual(package["status"], "available")
        self.assertEqual(package["payload"]["reportId"], self.report.id)
        self.assertEqual(analysis["status"], "available")
        self.assertEqual(keyword["status"], "available")
        self.assertEqual(budget["status"], "unavailable_no_fixed_budget")
        self.assertIsNone(budget["payload"])
        for item in (package, analysis, budget, keyword):
            self.assertEqual(item["reportId"], report_id)
            self.assertEqual(item["sourceReportId"], self.report.id)
            self.assertFalse(item["persistedRead"])
            self.assertFalse(item["sameJobProviderPersisted"])
            self.assertFalse(item["registeredAgentTool"])
            self.assertEqual(item["resultDigest"], digest({key: value
                for key, value in item.items() if key != "resultDigest"}))
        self.assertEqual((m.AiAgentJobs.objects.count(),
            m.AiAgentProviderDispatches.objects.count(),
            m.AiAgentToolDispatches.objects.count(),
            m.AiAgentToolResults.objects.count()), before)

    def test_wrong_report_role_and_mode_mix_reject_before_any_model_call(self):
        report_id, source = self.roots()
        common = {"reportId": report_id, "runId": source["evidenceRunId"],
            "screeningId": source["screeningIntent"]["id"]}
        for name, args in ((service.ORDER[3], {"reportId": report_id,
                "role": "commerce", "sourceKey": source["promotionSelector"]["sourceKey"],
                "view": "keyword_sku"}),
                (service.ORDER[2], {**common, "role": "market_b2b"}),
                (service.ORDER[1], {**common, "role": "commerce",
                    "mode": "mapped", "dimension": "sku", "sourceKey": "ads"}),
                (service.ORDER[3], {"reportId": report_id, "role": "promotion",
                    "sourceKey": source["promotionSelector"]["sourceKey"],
                    "view": "keyword_sku", "rowIndex": 0, "rowId": "0"*64,
                    "offset": 0})):
            with self.subTest(name=name, args=args), self.assertRaises(AiError):
                service.read(name, args, self.admin)
        with fixture.session_role("teruisi_ai_reader"), self.assertRaises(AiError):
            service.read(service.ORDER[0], {**common,
                "reportId": self.report.id, "role": "market_b2b"}, self.admin)

    def test_unpublished_screening_remains_unavailable_without_fake_ready(self):
        body = self.request_body()
        body["clientRequestId"] = uid("market-unpublished-source")
        with patch.object(promotion_creation.transport, "catalog",
                side_effect=self.current_catalog):
            created = promotion_creation.create(body, self.admin)
        source = m.AiReportRun.objects.get(pk=created["item"]["id"])
        self.assertFalse(m.AiBusinessScreeningRun.objects.filter(
            report_id=source.id).exists())
        parked_id = parked.create({"schemaVersion": parked.REQUEST_SCHEMA,
            "clientRequestId": "market-unpublished-parked",
            "sourceReportId": source.id,
            "marketSelector": self.selector()}, self.admin)["item"]["id"]
        prepared = market_material.prepare_candidate(parked_id, self.admin)
        self._attest_as_role(parked_id, prepared)
        admitted_id = admitted.create({"schemaVersion": admitted.REQUEST_SCHEMA,
            "clientRequestId": "market-unpublished-admitted",
            "parkedReportId": parked_id}, self.admin)["reportId"]
        snapshot = json.loads(source.snapshot_json)
        args = {"reportId": admitted_id,
            "runId": snapshot["evidenceRunId"],
            "screeningId": snapshot["screeningIntent"]["id"],
            "role": "market_b2b", "offset": 0}
        with fixture.session_role("teruisi_ai_reader"), self.assertRaisesRegex(
                AiError, "固定筛查结果不存在"):
            service.read(service.ORDER[0], args, self.admin)
        self.assertFalse(m.AiAgentJobs.objects.filter(
            workflow_run_id=m.AiReportRun.objects.get(pk=admitted_id).workflow_id
        ).exists())

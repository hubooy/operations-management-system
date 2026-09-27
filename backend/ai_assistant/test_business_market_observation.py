"""Owning sealed-page market observations; PostgreSQL-only integration tests."""
from copy import deepcopy
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from access_control.models import AppUser
from sales.auth import Principal
from business_analysis import mapping_plan
from business_analysis.contracts import AnalysisContractError
from . import business_market_observation as service
from . import test_business_market_dynamics as fixture
from .policy import AiError, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessMarketObservationTests(djtest.TransactionTestCase):
    user = fixture.BusinessMarketDynamicsTests.user
    call = fixture.BusinessMarketDynamicsTests.call
    bundle = fixture.BusinessMarketDynamicsTests.bundle
    input_for = fixture.BusinessMarketDynamicsTests.input_for
    insert = fixture.BusinessMarketDynamicsTests.insert
    seed = fixture.BusinessMarketDynamicsTests.seed
    collect_body = fixture.BusinessMarketDynamicsTests.collect_body
    market_row = fixture.BusinessMarketDynamicsTests.market_row

    def setUp(self):
        fixture.BusinessMarketDynamicsTests.setUp(self)
        self.old_report = self.report
        self.market_row(0, "2026-08-03", rank=1)
        self.market_row(77, "2026-08-03")
        body = deepcopy(self.fixed_body)
        body["clientRequestId"] = "market-observation-multiday"
        for source in body["sources"]:
            source["query"].update(startDate="2026-08-01", endDate="2026-08-03")
        self.parent = self.collect_body(body)
        self.sources = body["sources"]
        self.plan = mapping_plan.build(self.sources,
            [{"salesKey": "sales", "masterKey": "master"}])
        self.report, _ = self.seed()

    def params(self, **changes):
        return {"currentSourceKey": "market", "baselineSourceKey": "market-prior",
            "currentObservationDate": "2026-08-03",
            "baselineObservationDate": "2026-07-31", **changes}

    def test_complete_interval_pages_exact_row_and_no_live_business_query(self):
        with patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote, CaptureQueriesContext(
                connection) as queries:
            output = service.page(self.report.id, self.params(), self.admin)
        model.assert_not_called(); remote.assert_not_called()
        self.assertFalse(any("market_ranking_entries" in item["sql"].lower()
            for item in queries))
        self.assertEqual(output["binding"]["reportBinding"]["reportId"], self.report.id)
        self.assertEqual(output["table"]["observationCoverage"]["bothDatesPresent"], True)
        rows = {row["skuId"]: row for row in output["table"]["rows"]}
        self.assertEqual(rows["market-77"]["status"], "entered_observed_top_sample")
        self.assertIsNone(rows["market-77"]["baseline"]["metrics"])
        self.assertEqual(rows["market-90"]["status"], "left_observed_top_sample")
        self.assertFalse(output["authority"]["ownProductIdentityVerified"])
        row = rows["market-77"]
        found = service.read_row(self.report.id, "market", "market-prior",
            "2026-08-03", "2026-07-31", row["rowIndex"], row["rowId"], self.admin)
        self.assertEqual(found["row"], row)
        self.assertEqual(found["bindingDigest"], output["bindingDigest"])
        self.assertEqual(found["responseDigest"], digest({key: value for key, value
            in found.items() if key != "responseDigest"}))

    def test_missing_observation_day_is_not_top_exit_or_zero(self):
        value = service.page(self.report.id, self.params(
            currentObservationDate="2026-08-02",
            baselineObservationDate="2026-07-30"), self.admin)["table"]
        self.assertEqual(value["observationCoverage"], {"currentDatePresent": False,
            "baselineDatePresent": False, "bothDatesPresent": False})
        self.assertEqual(value["rows"], [])
        partial = service.page(self.report.id, self.params(
            currentObservationDate="2026-08-01",
            baselineObservationDate="2026-07-29"), self.admin)["table"]
        self.assertFalse(partial["observationCoverage"]["baselineDatePresent"])
        self.assertTrue(all(row["status"] == "insufficient_date_coverage"
            and row["baseline"]["status"] == "date_not_covered"
            and row["baseline"]["metrics"] is None for row in partial["rows"]))
        observed = service.page(self.report.id, self.params(), self.admin)["table"]
        self.assertEqual(next(row for row in observed["rows"]
            if row["skuId"] == "market-77")["baseline"]["status"],
            "not_observed_in_top_sample")

    def test_cross_report_source_day_digest_and_actor_reject(self):
        for params in (self.params(baselineSourceKey="missing"),
                       self.params(baselineSourceKey="market-other"),
                       self.params(baselineSourceKey="market"),
                       self.params(baselineObservationDate="2026-07-30"),
                       self.params(limit=10), self.params(extra="unknown")):
            with self.subTest(params=params), self.assertRaises(AiError):
                service.page(self.report.id, params, self.admin)
        with self.assertRaises(AiError):
            service.page(self.old_report.id, self.params(), self.admin)
        for actor in (self.viewer, self.user("other-market-observation@example.invalid", "admin", None),
                Principal(self.admin.email, "Scoped", "admin", {"shops": ["other"]})):
            with self.assertRaises(AiError):
                service.page(self.report.id, self.params(), actor)
        row = service.page(self.report.id, self.params(), self.admin)["table"]["rows"][0]
        with self.assertRaises(AiError):
            service.read_row(self.report.id, "market", "market-prior",
                "2026-08-03", "2026-07-31", row["rowIndex"], "0"*64, self.admin)
        with patch("ai_assistant.business_sealed.store.verify_seal",
                side_effect=AnalysisContractError("stale sealed digest")), self.assertRaises(AiError):
            service.page(self.report.id, self.params(), self.admin)

    def test_final_owner_revocation_blocks_prepared_row(self):
        original = service._response
        def revoke(*args, **kwargs):
            value = original(*args, **kwargs)
            AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            return value
        with patch.object(service, "_response", side_effect=revoke), self.assertRaises(AiError):
            service.page(self.report.id, self.params(), self.admin)

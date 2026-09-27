"""Report-bound v2 market selection from actual sealed Reader.info roots."""
from copy import deepcopy
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from access_control.models import AppUser
from business_analysis.contracts import comparison_periods, coverage

from . import business_promotion_market_admission as service
from . import test_business_promotion_formal_volumes as fixture
from .policy import AiError, digest


BANDS = [{"key":"low","lowerCents":0,"upperExclusiveCents":200},
    {"key":"high","lowerCents":200,"upperExclusiveCents":None}]


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionMarketAdmissionTests(djtest.TransactionTestCase):
    user = fixture.PromotionNineteenSourceVolumeTests.user
    request_body = fixture.PromotionNineteenSourceVolumeTests.request_body
    current_catalog = fixture.PromotionNineteenSourceVolumeTests.current_catalog
    create_fixed_report = fixture.PromotionNineteenSourceVolumeTests.create_fixed_report
    planned_evidence_body = fixture.PromotionNineteenSourceVolumeTests.planned_evidence_body

    def setUp(self):
        fixture.PromotionNineteenSourceVolumeTests.setUp(self)
        self.report = self.create_fixed_report()

    def selector(self, **changes):
        return {"priceBandSourceKey":"market-current",
            "rankCurrentSourceKey":"market-current",
            "rankBaselineKey":"market-previous", "bands":deepcopy(BANDS),
            "currentObservationDate":"2026-08-01",
            "baselineObservationDate":"2026-07-31", **changes}

    def test_exact_same_report_reader_info_binds_covered_days_without_market_sql(self):
        with patch("ai_assistant.transport.execute_tool") as remote, \
                CaptureQueriesContext(connection) as queries:
            result = service.require_observed(self.report.id,
                self.selector(), self.admin)
        remote.assert_not_called()
        self.assertFalse(any("market_ranking_entries" in row["sql"].lower()
            for row in queries))
        self.assertEqual(result["binding"]["reportId"], self.report.id)
        self.assertEqual(result["candidate"]["executionProfile"],
            service.market_contract.PROFILE)
        self.assertEqual(result["observationStatus"],
            {"current":"observed_date","baseline":"observed_date"})
        self.assertTrue(result["candidateEligible"])
        self.assertFalse(result["authorityVerified"])
        self.assertFalse(result["sourceCoverageVerified"])
        self.assertFalse(result["marketAndOwnSalesAdditive"])
        self.assertFalse(result["ownProductIdentityVerified"])
        self.assertEqual(result["bindingDigest"],digest({k:v for k,v in result.items()
            if k != "bindingDigest"}))

    def test_missing_day_has_exact_status_but_admission_refuses(self):
        original = service._roots
        def missing_baseline(*args, **kwargs):
            actor, fixed, sources, infos, context = original(*args,**kwargs)
            infos = deepcopy(infos)
            period = comparison_periods("2026-08-01","2026-08-01")["previous"]
            infos["market-previous"]["metadata"]["coverage"] = coverage(period, [])
            return actor, fixed, sources, infos, context
        with patch.object(service,"_roots",side_effect=missing_baseline):
            value = service.describe(self.report.id,self.selector(),self.admin)
            with self.assertRaises(AiError):
                service.require_observed(self.report.id,self.selector(),self.admin)
        self.assertEqual(value["observationStatus"]["baseline"],"date_not_covered")
        self.assertFalse(value["candidateEligible"])
        self.assertFalse(value["marketRowsReplayed"])

    def test_wrong_report_actor_market_key_category_scope_and_date_reject(self):
        outsider = self.user("market-admission-other@example.invalid","admin",None)
        for actor in (self.viewer, outsider):
            with self.assertRaises(AiError):
                service.describe(self.report.id,self.selector(),actor)
        for changed in (self.selector(rankBaselineKey="market-current"),
                self.selector(rankBaselineKey="missing"),
                self.selector(rankCurrentSourceKey="sales"),
                self.selector(currentObservationDate="2026-08-02")):
            with self.subTest(changed=changed), self.assertRaises(AiError):
                service.require_observed(self.report.id,changed,self.admin)
        original = service.report_binding._load
        for field in ("category","scope","rankingDimension","priceBandFilter"):
            def wrong_scope(*args, **kwargs):
                fixed, reader, request, mapping, sources, infos = original(*args,**kwargs)
                sources = deepcopy(sources)
                target = next(row for row in sources if row["key"] == "market-previous")
                target["query"][field] = "错类目" if field == "category" else "SELF"
                return fixed, reader, request, mapping, sources, infos
            with self.subTest(field=field), patch.object(service.report_binding,
                    "_load", side_effect=wrong_scope), self.assertRaises(AiError):
                service.describe(self.report.id,self.selector(),self.admin)

    def test_final_revocation_rejects(self):
        def revoke(event):
            if event == {"stage":"market_admission","phase":"before_final_fence"}:
                AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        with self.assertRaises(AiError):
            service.describe(self.report.id,self.selector(),self.admin,checkpoint=revoke)

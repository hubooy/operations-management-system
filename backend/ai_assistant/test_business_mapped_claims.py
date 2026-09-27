from copy import deepcopy
from contextlib import contextmanager
from unittest.mock import patch

from django.test import TestCase, override_settings

from business_analysis import mapping_plan
from . import business_mapped_analysis, business_mapped_claims, business_diagnosis
from . import test_business_identity as fixtures
from . import test_business_mapped_analysis as mapped_fixtures
from .business_evidence import get_run
from .business_sealed import Reader
from .policy import AiError


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MappedClaimsTests(TestCase):
    user = fixtures.BusinessIdentityTests.user
    call = fixtures.BusinessIdentityTests.call

    def setUp(self):
        fixtures.BusinessIdentityTests.setUp(self)
        sources = Reader(get_run(self.run_id, self.admin), self.admin).sources
        self.plan = mapping_plan.normalize(sources, [{"salesKey": "sales", "masterKey": "master"}])
        self.key = self.plan["pairs"][0]["pairKey"]
        with business_mapped_analysis.table(self.run_id, self.plan, self.key, "sku", self.admin) as table:
            rows = list(table.scan())
        row = next(row for row in rows if row["entity"]["mappingStatus"] == "matched")
        self.reference = {"pairKey": self.key, "dimension": "sku", "rowIndex": row["rowIndex"],
            "rowId": row["id"], "metric": "netSalesCents", "field": "value"}
        self.expected = row["metrics"]["netSalesCents"]["value"]

    def test_resolves_actual_mapped_value_and_preserves_mapping_provenance(self):
        with patch("ai_assistant.provider.turn") as model, patch("ai_assistant.transport.execute_tool") as remote:
            fact = business_mapped_claims.resolve(self.reference, self.run_id, self.plan, self.admin)
        self.assertEqual(fact["value"], self.expected)
        self.assertEqual(fact["reference"], self.reference)
        self.assertEqual(fact["mappingBinding"]["master"]["sourceKey"], "master")
        self.assertFalse(fact["historicalMapping"])
        self.assertIsNone(fact["baselineMappingBinding"])
        self.assertEqual(len(fact["mappingBindingDigest"]), 64)
        model.assert_not_called(); remote.assert_not_called()
        # The legacy diagnosis entry point cannot be made to accept mapped refs.
        with self.assertRaises(AiError):
            business_diagnosis.validate({"summary": "合成", "findings": [{"id": "f1", "kind": "observation",
                "title": "合成", "explanation": "合成事实", "references": [self.reference]}]}, self.run_id, self.admin)

    def test_invalid_or_unavailable_claim_cannot_invent_value(self):
        for change in ({"rowId": "a"*64}, {"pairKey": "a"*64}, {"rowIndex": True}, {"rowIndex": 249999},
                {"metric": "keywordProfit"}, {"field": "ratio"}, {"field": "changeRate"},
                {"dimension": "keyword"}, {"sourceKey": "sales"}, {"value": 999999},
                {"baselinePairKey": self.key, "field": "baseline"}):
            with self.subTest(change=change), self.assertRaises(AiError):
                business_mapped_claims.resolve({**self.reference, **change}, self.run_id, self.plan, self.admin)
        with self.assertRaises(AiError):
            business_mapped_claims.resolve(self.reference, self.run_id, self.plan, self.viewer)
        plan = deepcopy(self.plan)
        plan["pairs"][0]["masterKey"] = "sales"
        with self.assertRaises(AiError):
            business_mapped_claims.resolve(self.reference, self.run_id, plan, self.admin)

    def test_actual_comparison_claims_and_late_failure_do_not_invent_facts(self):
        mapped_fixtures.BusinessMappedAnalysisTests.prior(self)
        run_id = mapped_fixtures.BusinessMappedAnalysisTests.seed(self, baseline=True)
        plan, keys = mapped_fixtures.BusinessMappedAnalysisTests.fixed_plan(self, run_id, baseline=True)
        with business_mapped_analysis.table(run_id, plan, keys["sales"], "sku", self.admin,
                baseline_pair_key=keys["previous"]) as table:
            rows = list(table.scan())
        matched = next(row for row in rows if row["entity"]["mappingStatus"] == "matched")
        reference = {**self.reference, "pairKey": keys["sales"], "baselinePairKey": keys["previous"],
            "rowId": matched["id"], "rowIndex": matched["rowIndex"]}
        for field in ("baseline", "difference", "changeRate"):
            claim = business_mapped_claims.resolve({**reference, "field": field}, run_id, plan, self.admin)
            self.assertEqual(claim["value"], matched["comparisons"]["netSalesCents"][field])
            self.assertIsNotNone(claim["value"])
            self.assertIsNotNone(claim["baselineMappingBinding"])
        unmatched = next(row for row in rows if row["entity"]["mappingStatus"] == "unmatched")
        with self.assertRaises(AiError):
            business_mapped_claims.resolve({**reference, "field": "baseline", "rowId": unmatched["id"],
                "rowIndex": unmatched["rowIndex"]}, run_id, plan, self.admin)
        original = business_mapped_analysis.table
        @contextmanager
        def late(*args, **kwargs):
            with original(*args, **kwargs) as table:
                yield table
            raise AiError("synthetic late permission loss", "access_denied", 403)
        with patch.object(business_mapped_analysis, "table", late), self.assertRaises(AiError):
            business_mapped_claims.resolve(self.reference, self.run_id, self.plan, self.admin)

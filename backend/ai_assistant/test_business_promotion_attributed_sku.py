"""Isolated sealed-report checks for a distinct followed-SKU source view."""
from copy import deepcopy
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from access_control.models import AppUser

from business_analysis.contracts import AnalysisContractError
from . import business_promotion_attributed_sku as service
from . import test_business_promotion_keyword_sku as fixture
from .policy import AiError


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",
    DJANGO_ENVIRONMENT="test")
class BusinessPromotionAttributedSkuTests(djtest.TransactionTestCase):
    user = fixture.BusinessPromotionKeywordSkuTests.user
    call = fixture.BusinessPromotionKeywordSkuTests.call
    bundle = fixture.BusinessPromotionKeywordSkuTests.bundle
    input_for = fixture.BusinessPromotionKeywordSkuTests.input_for
    insert = fixture.BusinessPromotionKeywordSkuTests.insert
    seed = fixture.BusinessPromotionKeywordSkuTests.seed
    collect_body = fixture.BusinessPromotionKeywordSkuTests.collect_body
    ad = fixture.BusinessPromotionKeywordSkuTests.ad
    setUp = fixture.BusinessPromotionKeywordSkuTests.setUp

    def test_full_followed_sku_context_conserves_selected_source_only(self):
        params = {"sourceKey": "ads", "baselineKey": "ads-previous",
            "view": "keyword_searchterm_plan_unit_match_attributed_sku"}
        with patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote, \
                CaptureQueriesContext(connection) as queries:
            result = service.page(self.report.id, params, self.admin)
        model.assert_not_called(); remote.assert_not_called()
        self.assertFalse(any("netshop_rows" in item["sql"].lower()
            or "sales_order_lines" in item["sql"].lower()
            for item in queries))
        rows = result["table"]["rows"]
        self.assertEqual(sum(row["currentRowCount"] or 0 for row in rows), 5)
        self.assertEqual(sum(row["metrics"]["spendCents"]["value"]
            for row in rows if row["metrics"]), 1050)
        self.assertTrue(all(row["entity"]["attributedSkuId"] == "ATTRIBUTED"
            for row in rows))
        self.assertTrue(all("promotedSkuId" not in row["entity"]
            and "triggerSkuId" not in row["entity"] for row in rows))
        self.assertTrue(result["authority"][
            "completeSourceTraversalForSelectedSources"])
        self.assertFalse(result["authority"]["productMasterIdentityVerified"])
        self.assertFalse(result["authority"]["agentReadPersisted"])
        self.assertFalse(result["table"]["crossViewAdditive"])
        self.assertEqual(result["binding"]["reportBinding"]["reportId"],
            self.report.id)

    def test_exact_row_and_wrong_actor_or_revocation_refuse(self):
        params = {"sourceKey": "ads", "view": "keyword_attributed_sku"}
        value = service.page(self.report.id, params, self.admin)
        selected = value["table"]["rows"][0]
        exact = service.read_row(self.report.id, "ads", params["view"],
            selected["rowIndex"], selected["id"], self.admin)
        self.assertEqual(exact["row"], selected)
        with self.assertRaises(AiError):
            service.read_row(self.report.id, "ads", params["view"],
                selected["rowIndex"], "0" * 64, self.admin)
        with self.assertRaises(AiError):
            service.page(self.report.id, params, self.viewer)
        AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        with self.assertRaises(AiError):
            service.page(self.report.id, params, self.admin)

    def test_late_persisted_page_mutation_is_rejected_without_partial_row(self):
        original = service.report_binding.Reader.pages
        def changed(reader, key, *args, **kwargs):
            pages = list(original(reader, key, *args, **kwargs))
            if key == "ads":
                pages[-1] = deepcopy(pages[-1])
                pages[-1]["items"][0]["dimensions"]["attributedSkuId"] = \
                    "FORGED"
            yield from pages
        with patch.object(service.report_binding.Reader, "pages", changed):
            with self.assertRaises(AiError):
                service.page(self.report.id, {"sourceKey": "ads",
                    "view": "keyword_attributed_sku"}, self.admin)

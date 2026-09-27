"""Actual sealed PG report export materials; no renderer or model calls."""
from copy import deepcopy
import hashlib
import json
from unittest.mock import patch
from urllib.parse import urlencode
from django import test as djtest
from django.http import QueryDict
from access_control.models import AppUser
from netshop.analysis_continuation import read_page as continuation_page
from netshop.models import NetshopRow
from . import business_promotion_export as service
from . import test_business_promotion_keyword_sku as fixtures
from .test_business_evidence import versioned_netshop_facts
from .policy import AiError, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionExportTests(djtest.TransactionTestCase):
    user = fixtures.BusinessPromotionKeywordSkuTests.user
    call = fixtures.BusinessPromotionKeywordSkuTests.call
    bundle = fixtures.BusinessPromotionKeywordSkuTests.bundle
    input_for = fixtures.BusinessPromotionKeywordSkuTests.input_for
    insert = fixtures.BusinessPromotionKeywordSkuTests.insert
    seed = fixtures.BusinessPromotionKeywordSkuTests.seed
    collect_body = fixtures.BusinessPromotionKeywordSkuTests.collect_body
    ad = fixtures.BusinessPromotionKeywordSkuTests.ad
    setUp = fixtures.BusinessPromotionKeywordSkuTests.setUp

    def prepare(self, **kwargs):
        return service.prepare(self.report.id, "ads", self.admin, baseline_key="ads-previous", **kwargs)

    def rows(self, result, view):
        return [json.loads(line) for raw in result.ndjson_pages(view) for line in raw.splitlines()]

    def test_complete_two_views_hashes_missing_identity_and_expenses_not_additive(self):
        with patch("ai_assistant.provider.turn") as model, patch("ai_assistant.transport.execute_tool") as remote:
            result = self.prepare()
        model.assert_not_called(); remote.assert_not_called()
        manifest = result.manifest
        self.assertFalse(manifest["tableExpensesAreAdditive"])
        self.assertFalse(manifest["registeredRenderer"])
        self.assertEqual(manifest["manifestDigest"], digest({k:v for k,v in manifest.items() if k != "manifestDigest"}))
        for table in manifest["tables"]:
            raw = b"".join(result.ndjson_pages(table["view"]))
            self.assertEqual(hashlib.sha256(raw).hexdigest(), table["ndjsonSha256"])
            self.assertEqual(len(raw), table["ndjsonBytes"])
            self.assertEqual(len(self.rows(result, table["view"])), table["rowCount"])
            self.assertGreater(table["missingPromotedSkuGroups"], 0)
            self.assertEqual(table["spendTotals"]["current"]["value"], 1050)
        manifest["tables"].clear()
        self.assertEqual(len(result.manifest["tables"]), 2)

    def test_over_100_keyword_sku_rows_complete_pages_negative_values_and_multiple_plans(self):
        with versioned_netshop_facts():
            for index in range(20,125):
                self.ad(index, "PLAN-"+str(index%3), "UNIT-"+str(index), "精确", -5 if index == 20 else 10)
                row = NetshopRow.objects.get(source_row_key="integrated-ad-"+str(index))
                row.raw_json.update({"关键词": "同一个词", "智能投放推广SKU ID": "SKU-"+str(index)})
                row.source_row_hash = digest([row.raw_json, row.spend_cents])
                row.save(update_fields=["raw_json", "source_row_hash"])
        # This source needs a second page. Exercise the actual owning continuation
        # instead of making the old fixture bypass its persisted checkpoint gate.
        self.source_tools.append({**self.source_tools[0], "name": "get_business_netshop_continuation_page"})
        previous_execute = self.source_execute
        def execute(name, args, actor, **kwargs):
            if name == "get_business_netshop_continuation_page":
                return {"toolName": name, "ok": True, "auditStatus": "recorded",
                    "data": continuation_page(actor, QueryDict(urlencode(args)))}
            return previous_execute(name, args, actor, **kwargs)
        self.source_execute = execute
        body = deepcopy(self.fixed_body); body["clientRequestId"] = "promotion-material-wide"
        self.parent = self.collect_body(body); self.report, _ = self.seed()
        prepared = self.prepare()
        for table in prepared.manifest["tables"]:
            self.assertGreater(table["rowCount"], 100)
            self.assertGreater(table["pageCount"], 5)
            rows = self.rows(prepared, table["view"])
            self.assertEqual([r["rowIndex"] for r in rows], list(range(len(rows))))
            self.assertTrue(any((r["metrics"] or {}).get("spendCents", {}).get("value") == -5 for r in rows))
            self.assertEqual(table["spendTotals"]["current"]["value"], 2085)

    def test_capacity_is_all_or_nothing_and_limits_cannot_expand(self):
        for bounds in ({"maxRows": 1}, {"maxPages": 1}, {"maxBytes": 100}):
            with self.assertRaises(AiError) as caught: self.prepare(limits=bounds)
            self.assertEqual(caught.exception.status, 413)
        for bounds in ({"maxRows": True}, {"maxBytes": service.MAX_BYTES+1}, {"extra": 1}):
            with self.assertRaises(AiError): self.prepare(limits=bounds)

    def test_final_revoke_or_cancellation_never_returns_prepared_material(self):
        error = RuntimeError("export cancelled")
        def cancel(event):
            if event.get("stage") == "promotion_export": raise error
        with self.assertRaises(RuntimeError) as caught: self.prepare(checkpoint=cancel)
        self.assertIs(caught.exception, error)
        def revoke(event):
            if event.get("stage") == "promotion_export" and event.get("phase") == "complete":
                AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        with self.assertRaises(AiError): self.prepare(checkpoint=revoke)

    def test_tampered_table_page_is_not_a_valid_export(self):
        from business_analysis import promotion_keyword_sku
        original = promotion_keyword_sku._Table.page
        def changed(table, *args, **kwargs):
            page = original(table, *args, **kwargs)
            if page["rows"]: page["pageDigest"] = "0"*64
            return page
        with patch.object(promotion_keyword_sku._Table, "page", changed), self.assertRaises(AiError): self.prepare()

    def test_wrong_report_owner_and_source_are_rejected(self):
        with self.assertRaises(AiError): service.prepare(self.report.id, "sales", self.admin)
        with self.assertRaises(AiError): service.prepare(self.report.id, "ads", self.viewer)
        with self.assertRaises(AiError): service.prepare("missing", "ads", self.admin)

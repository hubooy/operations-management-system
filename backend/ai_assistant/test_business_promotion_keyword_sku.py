"""Actual sealed JD facts, not AST metadata fixtures. Root runs PostgreSQL."""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from django import test as djtest
from django.db import connection
from django.http import QueryDict
from django.test.utils import CaptureQueriesContext
from netshop import analysis_continuation as netshop_continuation
from netshop.models import NetshopRow

from business_analysis.contracts import AnalysisContractError
from . import business_promotion_keyword_sku as service
from . import business_collection_continuation as collection_continuation
from . import test_business_promotion_views as fixtures
from .test_business_evidence import versioned_netshop_facts
from .policy import AiError, canonical, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessPromotionKeywordSkuTests(djtest.TransactionTestCase):
    # Reuse only fixture methods, never inherit/import another TestCase class.
    user = fixtures.BusinessPromotionViewTests.user
    call = fixtures.BusinessPromotionViewTests.call
    bundle = fixtures.BusinessPromotionViewTests.bundle
    input_for = fixtures.BusinessPromotionViewTests.input_for
    insert = fixtures.BusinessPromotionViewTests.insert
    seed = fixtures.BusinessPromotionViewTests.seed
    collect_body = fixtures.BusinessPromotionViewTests.collect_body

    def ad(self, number, plan, unit, match, amount, **kwargs):
        if not connection.in_atomic_block:
            with versioned_netshop_facts():
                return self.ad(number, plan, unit, match, amount, **kwargs)
        fixtures.BusinessPromotionViewTests.ad(self, number, plan, unit, match, amount, **kwargs)
        row = NetshopRow.objects.get(source_row_key="integrated-ad-"+str(number))
        keyword = None if number == 4 else "切肉机" if number != 2 else "绞肉机"
        sku = None if number == 4 else "PROMOTED-" + str(number if number in (2, 3) else 1)
        row.raw_json.update({"关键词": keyword, "智能投放推广SKU ID": sku,
            "搜索词": "不同搜索词", "触发SKU ID": "TRIGGER", "跟单SKU ID": "ATTRIBUTED"})
        # Deliberately reproduce the old generic SKU's distinct role.
        row.sku_id = "TRIGGER"
        row.source_row_hash = digest([row.raw_json, amount, row.business_date])
        row.save(update_fields=["raw_json", "sku_id", "source_row_hash"])

    def setUp(self):
        fixtures.BusinessPromotionViewTests.setUp(self)

    def page(self, **params):
        return service.page(self.report.id, {"sourceKey": "ads", "view": "keyword_sku", **params}, self.admin)

    def test_real_reader_joint_context_and_baseline_conserve_amounts(self):
        for view, amounts in (("keyword_sku", [50, 300, 300, 400]),
                ("keyword_sku_context", [50, 100, 200, 300, 400])):
            result = self.page(view=view, baselineKey="ads-previous")
            rows = result["table"]["rows"]
            self.assertEqual(sorted(row["metrics"]["spendCents"]["value"] for row in rows), amounts)
            self.assertEqual(sum(row["currentRowCount"] for row in rows), 5)
            self.assertEqual(sum(row["metrics"]["spendCents"]["value"] for row in rows), 1050)
            self.assertTrue(result["authority"]["completeSourceTraversalForSelectedSources"])
            self.assertFalse(result["authority"]["productMasterIdentityVerified"])
            self.assertFalse(result["table"]["authorityVerified"])
            self.assertEqual(result["binding"]["reportBinding"]["reportId"], self.report.id)
            for row in rows:
                self.assertNotEqual(row["entity"]["promotedSkuId"], "TRIGGER")
                if not row["identityQualified"]:
                    self.assertEqual(row["comparisons"]["spendCents"]["status"], "unavailable")
        selected = next(row for row in self.page(baselineKey="ads-previous")["table"]["rows"]
            if row["entity"]["promotedSkuId"] == "PROMOTED-1")
        self.assertEqual(selected["comparisons"]["spendCents"]["difference"], 250)

    def test_complete_both_streams_once_no_business_queries_writes_or_models(self):
        calls, finished = [], []
        original = service.report_binding.Reader.pages
        def tracked(reader, key, *args, **kwargs):
            calls.append(key)
            yield from original(reader, key, *args, **kwargs)
            finished.append(key)
        with patch.object(service.report_binding.Reader, "pages", tracked), patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote, CaptureQueriesContext(connection) as queries:
            with service.table(self.report.id, "ads", "keyword_sku", self.admin, baseline_key="ads-previous") as (opened, binding):
                self.assertEqual(calls, ["ads", "ads-previous"])
                self.assertEqual(finished, calls)
                opened.page(); list(opened.scan()); opened.page()
                self.assertEqual(calls, ["ads", "ads-previous"])
                self.assertEqual(binding["algorithmVersion"], "promotion-keyword-promoted-sku-v1")
        model.assert_not_called(); remote.assert_not_called()
        for query in queries:
            self.assertNotIn("netshop_rows", query["sql"].lower())
            self.assertNotIn("sales_order_lines", query["sql"].lower())
            self.assertFalse(query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")))
        with self.assertRaises(AnalysisContractError): opened.header()

    def test_explicit_missing_columns_preserve_amount_not_generic_sku(self):
        with versioned_netshop_facts():
            for row in NetshopRow.objects.filter(source="jd_promotion", shop_name=self.query["shop"]):
                row.raw_json.pop("关键词", None); row.raw_json.pop("智能投放推广SKU ID", None)
                row.source_row_hash = digest(["missing", row.raw_json, row.pk])
                row.save(update_fields=["raw_json", "source_row_hash"])
        body = deepcopy(self.fixed_body); body["clientRequestId"] = "joint-missing-columns"
        self.parent = self.collect_body(body); self.report, _ = self.seed()
        result = self.page(baselineKey="ads-previous")
        coverage = result["table"]["identityCoverage"]["current"]
        self.assertEqual(coverage, {"rowCount": 5, "qualifiedRows": 0, "unqualifiedRows": 5})
        row = result["table"]["rows"][0]
        self.assertEqual(row["metrics"]["spendCents"]["value"], 1050)
        self.assertFalse(row["identityQualified"])
        self.assertEqual(row["missingIdentityFields"], ["keyword", "promotedSkuId"])
        self.assertEqual(row["comparisons"]["spendCents"]["status"], "unavailable")

    def test_exact_row_ids_digests_copy_and_cross_view_rejection(self):
        page = self.page(); row = page["table"]["rows"][0]
        result = service.read_row(self.report.id, "ads", "keyword_sku", row["rowIndex"], row["id"], self.admin)
        self.assertEqual(result["row"], row)
        self.assertEqual(result["bindingDigest"], page["bindingDigest"])
        self.assertEqual(result["responseDigest"], digest({k:v for k,v in result.items() if k != "responseDigest"}))
        for view, before in (("keyword_sku_context", None), ("keyword_sku", "ads-previous")):
            with self.assertRaises(AiError):
                service.read_row(self.report.id, "ads", view, row["rowIndex"], row["id"], self.admin, baseline_key=before)
        with self.assertRaises(AiError): service.read_row(self.report.id, "ads", "keyword_sku", 0, "0"*64, self.admin)
        result["row"]["metrics"]["spendCents"]["value"] = 99
        self.assertEqual(self.page(), page)

    def test_owner_scope_wrong_domain_baseline_and_types_fail_before_facts(self):
        from sales.auth import Principal
        for actor in (self.viewer, self.user("joint-other@example.invalid", "admin", None),
                Principal(self.admin.email, "scoped", "admin", {"shops": ["other"]})):
            with self.assertRaises(AiError): service.page(self.report.id, {"sourceKey": "ads", "view": "keyword_sku"}, actor)
        def forbidden(*args, **kwargs):
            self.fail("invalid source must fail before traversal")
            yield None
        for params in ({"sourceKey": "sales"}, {"sourceKey": "master"}, {"sourceKey": "missing"},
                {"view": "sku"}, {"baselineKey": "ads-other"}, {"baselineKey": "ads"},
                {"offset": True}, {"offset": 1.0}, {"offset": "0"}, {"limit": 10}, {"unknown": True}):
            with self.subTest(params=params), patch.object(service.report_binding.Reader, "pages", forbidden), self.assertRaises(AiError):
                self.page(**params)

    def test_tail_error_and_late_revocation_never_return_authority(self):
        original = service.report_binding.Reader.pages
        def broken(reader, key, *args, **kwargs):
            yield from original(reader, key, *args, **kwargs)
            raise AiError("synthetic source tail failure")
        with patch.object(service.report_binding.Reader, "pages", broken), self.assertRaisesRegex(AiError, "tail"):
            self.page()
        pure = service.promotion_keyword_sku.table
        @contextmanager
        def revoked(*args, **kwargs):
            with pure(*args, **kwargs) as result:
                yield result
            from access_control.models import AppUser
            AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        with patch.object(service.promotion_keyword_sku, "table", revoked), self.assertRaises(AiError):
            self.page()

    def test_real_checkpoint_cancel_and_suspended_scan_cleanup(self):
        cancelled = AiError("synthetic cancellation", "cancelled", 409)
        def callback(event):
            if event.get("phase") == "source_complete": raise cancelled
        with self.assertRaises(AiError) as caught:
            service.page(self.report.id, {"sourceKey": "ads", "view": "keyword_sku"}, self.admin, checkpoint=callback)
        self.assertIs(caught.exception, cancelled)
        with service.table(self.report.id, "ads", "keyword_sku", self.admin) as (opened, _):
            path = Path(opened._store.directory.name)
            scan = opened.scan(); next(scan)
        self.assertFalse(path.exists())
        with self.assertRaises(AnalysisContractError): next(scan)

    def test_wide_complete_prefix_pagination_and_no_silent_truncation(self):
        with versioned_netshop_facts():
            for number in range(20, 43):
                self.ad(number, "计"*180, "单"*180, "精"*180, number)
                row = NetshopRow.objects.get(source_row_key="integrated-ad-"+str(number))
                row.raw_json.update({"关键词": "词"*180+str(number), "智能投放推广SKU ID": "货"*180})
                row.source_row_hash = digest([number, row.raw_json])
                row.save(update_fields=["raw_json", "source_row_hash"])
        body = deepcopy(self.fixed_body); body["clientRequestId"] = "joint-wide"
        original_execute = self.source_execute
        tool_name = collection_continuation.TOOL
        self.source_tools = [*self.source_tools,
            {**deepcopy(self.source_tools[0]), "name": tool_name}]
        def complete_source(name, arguments, principal, **kwargs):
            if name != tool_name:
                return original_execute(name, arguments, principal, **kwargs)
            self.assertEqual(kwargs["surface"], "business_collection")
            self.assertNotIn("domain", arguments)
            page = netshop_continuation.read_page(principal,
                QueryDict(urlencode(arguments)))
            return {"toolName": name, "ok": True, "auditStatus": "recorded",
                "data": page}
        self.source_execute = complete_source
        self.parent = self.collect_body(body); self.report, _ = self.seed()
        actual, offset = [], 0
        with patch.object(service, "MAX_RESPONSE_BYTES", 16000):
            while offset is not None:
                result = self.page(view="keyword_sku_context", offset=offset)
                self.assertLessEqual(len(canonical(result).encode("utf-8")), 16000)
                part = result["table"]
                self.assertEqual(part["pageDigest"], digest({k:v for k,v in part.items() if k != "pageDigest"}))
                actual.extend(part["rows"]); offset = part["pagination"]["nextOffset"]
        with service.table(self.report.id, "ads", "keyword_sku_context", self.admin) as (opened, _):
            self.assertEqual(actual, list(opened.scan()))
        self.assertEqual(len(actual), 28)

    def test_sealed_snapshot_and_page_tampering_rejected(self):
        load = service.report_binding._load
        calls = []
        def altered(report_id, principal):
            result = load(report_id, principal); calls.append(report_id)
            if len(calls) == 1: result[0]["snapshotDigest"] = "0"*64
            return result
        with patch.object(service.report_binding, "_load", altered), self.assertRaises(AiError): self.page()
        original = service.report_binding.Reader.pages
        def damaged(reader, key, *args, **kwargs):
            for page in original(reader, key, *args, **kwargs):
                if key == "ads" and page["items"]: page["items"][0]["dimensions"]["promotedSkuId"] = "FORGED"
                yield page
        with patch.object(service.report_binding.Reader, "pages", damaged), self.assertRaises(AiError): self.page()

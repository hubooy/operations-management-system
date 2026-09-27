from copy import deepcopy
from contextlib import contextmanager
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.db import connection, transaction, DatabaseError
from sales.tests.factories import make_line, signed_headers, TEST_SECRET
from sales.analysis import read_page
from business_analysis.contracts import SCHEMA_VERSION, digest as page_digest, comparison_periods
from business_analysis.identity import product_reconciliation
from . import tests as fixtures, models as m, business_evidence as evidence
from .policy import AiError, canonical, digest


@contextmanager
def versioned_netshop_facts():
    """Commit synthetic owning rows with a same-transaction source revision."""
    from netshop.models import NetshopDataRevision
    with transaction.atomic():
        revision, _ = NetshopDataRevision.objects.select_for_update().get_or_create(
            domain="netshop", defaults={"revision": 0, "source_digest": "a" * 64})
        yield
        revision.refresh_from_db()
        revision.revision += 1
        revision.source_digest = digest(["synthetic-netshop-facts",
            revision.source_digest, revision.revision])
        revision.save(update_fields=["revision", "source_digest", "updated_at"])


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessEvidenceTests(TestCase):
    user = fixtures.AiDomainTests.user
    call = fixtures.AiDomainTests.call

    def setUp(self):
        fixtures.AiDomainTests.setUp(self)
        self.admin = self.user("evidence@example.invalid", "admin", None)
        self.query = {"platform": "京东", "shop": "京东一店", "channel": "京东-京东一店", "startDate": "2026-08-01", "endDate": "2026-08-01"}
        self.body = {"clientRequestId": "evidence-client", "sources": [{"key": "sales", "domain": "sales", "query": self.query}]}
        for i in range(12):
            make_line(i+1, f"evidence-{i}", channel=self.query["channel"], online_spec_code="M1").save()
        self.catalog = [fixtures.CATALOG[0], {**fixtures.CATALOG[0], "name": "get_sales_analysis_records"}, {**fixtures.CATALOG[0], "name": "get_netshop_analysis_records"}]

    def execute(self, name, args, principal, **kwargs):
        data = {"dataCutoffDate": "2026-08-01"} if name == "get_data_freshness" else read_page(principal, {"operation": "analysis_records", **args})
        return {"toolName": name, "ok": True, "auditStatus": "recorded", "data": data}

    def collect(self, run_id, version):
        with patch("ai_assistant.transport.catalog", return_value=self.catalog), patch("ai_assistant.transport.execute_tool", side_effect=self.execute):
            return evidence.collect(run_id, {"sourceKey": "sales", "expectedVersion": version}, self.admin, "test-read")

    def test_durable_pages_resume_seal_and_idempotent_creation(self):
        result = evidence.create(self.body, self.admin)
        run_id = result["item"]["id"]
        self.assertTrue(evidence.create(self.body, self.admin)["replayed"])
        with self.assertRaises(AiError):
            evidence.finish(run_id, {"expectedVersion": 1, "action": "seal"}, self.admin)
        first = self.collect(run_id, 1)["item"]
        self.assertFalse(first["sources"]["sales"]["complete"])
        with self.assertRaises(AiError):
            self.collect(run_id, 1)
        # New function invocation restores only persisted state, not local objects.
        second = self.collect(run_id, 2)["item"]
        self.assertEqual(second["sources"]["sales"]["reconciliation"]["metrics"]["netSalesCents"]["value"], 120000)
        sealed = evidence.finish(run_id, {"expectedVersion": 3, "action": "seal"}, self.admin)["item"]
        self.assertEqual(sealed["status"], "sealed")
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.filter(run_id=run_id).count(), 2)
        with patch("ai_assistant.transport.execute_tool") as remote:
            table = evidence.analysis_table(run_id, {"sourceKey": "sales", "dimension": "shop"}, self.admin)
            self.assertEqual(table["rows"][0]["metrics"]["netSalesCents"]["value"], 120000)
            self.assertFalse(table["pagination"]["hasMore"])
            tail = evidence.analysis_table(run_id, {"sourceKey": "sales", "dimension": "shop", "offset": "30000"}, self.admin)
            self.assertEqual(tail["rows"], [])
            self.assertEqual(tail["total"], table["total"])
            self.assertFalse(tail["pagination"]["hasMore"])
            remote.assert_not_called()
        url = f"/api/ai/business-evidence/{run_id}/analysis?sourceKey=sales&dimension=shop"
        with patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}), override_settings(DJANGO_INTERNAL_SECRET=TEST_SECRET, DJANGO_PROCESS_ROLE="ai_reader"), patch("ai_assistant.views.authority"):
            denied = self.client.get(url, headers=signed_headers(url, email=self.viewer.email, role="viewer"))
            self.assertEqual(denied.status_code, 403)
            response = self.client.get(url, headers=signed_headers(url, email=self.admin.email))
            self.assertEqual(response.status_code, 200, response.content)
        chunk = evidence.chunk(run_id, "sales", {"sequence": "2"}, self.admin)
        self.assertEqual(chunk["page"]["pageEvidence"]["rowCount"], 2)
        with self.assertRaises(AiError):
            self.collect(run_id, 4)

    def test_bad_page_capacity_and_remote_failure_preserve_checkpoint(self):
        run_id = evidence.create(self.body, self.admin)["item"]["id"]
        def wrong(*args, **kwargs):
            result = self.execute(*args, **kwargs)
            if args[0] != "get_data_freshness":
                result["data"]["filters"]["shop"] = "其他店"
            return result
        with patch("ai_assistant.transport.catalog", return_value=self.catalog), patch("ai_assistant.transport.execute_tool", side_effect=wrong), self.assertRaises(AiError):
            evidence.collect(run_id, {"sourceKey": "sales", "expectedVersion": 1}, self.admin, "bad")
        with patch("ai_assistant.business_evidence.MAX_BYTES", 1), self.assertRaises(AiError):
            self.collect(run_id, 1)
        self.assertEqual(evidence.get_run(run_id, self.admin).version, 1)
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.count(), 0)
        self.collect(run_id, 1)
        with patch("ai_assistant.transport.catalog", return_value=self.catalog), patch("ai_assistant.transport.execute_tool", side_effect=AiError("unavailable")), self.assertRaises(AiError):
            evidence.collect(run_id, {"sourceKey": "sales", "expectedVersion": 2}, self.admin, "failed")
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.count(), 1)

    def test_market_sample_persists_without_store_identity_or_point_estimates(self):
        from market.analysis import read_page as market_page
        from market.models import MarketRankingEntry
        MarketRankingEntry.objects.create(natural_key="synthetic-market", source_row_number=1,
            period_start="2026-08-01", period_end="2026-08-01", category="饮水机", scope="POP",
            ranking_dimension="SKU", price_band_filter="全部", sku_code="SKU1", product_name="合成",
            brand="合成品牌", rank=1, gmv_cents=99999, gmv_low_cents=100, gmv_high_cents=300, last_import_batch_id="synthetic")
        query = {"platform": "京东", "category": "饮水机", "scope": "POP", "rankingDimension": "SKU",
            "priceBandFilter": "全部", "startDate": "2026-08-01", "endDate": "2026-08-01"}
        run_id = evidence.create({"clientRequestId": "market-evidence", "sources": [{"key": "market", "domain": "market", "query": query}]}, self.admin)["item"]["id"]
        def execute(name, args, principal, **kwargs):
            if name != "get_market_analysis_records":
                return self.execute(name, args, principal, **kwargs)
            return {"toolName": name, "ok": True, "auditStatus": "recorded", "data": market_page(principal, {"operation": "analysis_records", **args})}
        catalog = self.catalog + [{**fixtures.CATALOG[0], "name": "get_market_analysis_records"}]
        with patch("ai_assistant.transport.catalog", return_value=catalog), patch("ai_assistant.transport.execute_tool", side_effect=execute):
            evidence.collect(run_id, {"sourceKey": "market", "expectedVersion": 1}, self.admin, "market")
        evidence.finish(run_id, {"expectedVersion": 2, "action": "seal"}, self.admin)
        with patch("ai_assistant.transport.execute_tool") as remote:
            table = evidence.analysis_table(run_id, {"sourceKey": "market", "dimension": "brand"}, self.admin)
            remote.assert_not_called()
        row = table["rows"][0]
        self.assertEqual(row["metrics"]["sampleGmvLowerCents"]["value"], 100)
        self.assertEqual(row["metrics"]["sampleGmvUpperCents"]["value"], 300)
        self.assertNotIn("99999", canonical(table))

    def test_bulk_mode_pins_collector_and_keeps_legacy_cursor_contract(self):
        body = {**self.body, "collectionMode": "bulk", "clientRequestId": "bulk"}
        run_id = evidence.create(body, self.admin)["item"]["id"]
        catalog = [fixtures.CATALOG[0], {**fixtures.CATALOG[0], "name": "get_business_source_page"}]
        def execute(name, args, principal, **kwargs):
            self.assertEqual(kwargs["surface"], "business_collection")
            if name == "get_data_freshness":
                return self.execute(name, args, principal, **kwargs)
            self.assertEqual(args["limit"], 100)
            self.assertEqual(args["domain"], "sales")
            query = {k: v for k, v in args.items() if k != "domain"}
            return {"toolName": name, "ok": True, "auditStatus": "recorded", "data": read_page(principal, {"operation": "analysis_records", **query})}
        with patch("ai_assistant.transport.catalog", return_value=catalog) as load, patch("ai_assistant.transport.execute_tool", side_effect=execute):
            result = evidence.collect(run_id, {"sourceKey": "sales", "expectedVersion": 1}, self.admin, "bulk")
            load.assert_called_once_with(self.admin, "business_collection")
        self.assertEqual(result["item"]["sources"]["sales"]["rowCount"], 12)
        self.assertTrue(result["item"]["sources"]["sales"]["complete"])
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.filter(run_id=run_id).count(), 1)
        first = evidence.chunk(run_id, "sales", {"sequence": "1", "rowOffset": "0", "rowLimit": "10"}, self.admin)
        second = evidence.chunk(run_id, "sales", {"sequence": "1", "rowOffset": "10", "rowLimit": "10"}, self.admin)
        self.assertFalse(first["completeChunkInResponse"])
        self.assertEqual(first["rowPagination"]["nextOffset"], 10)
        self.assertEqual(len(first["items"]) + len(second["items"]), 12)
        self.assertEqual(first["payloadDigest"], second["payloadDigest"])
        self.assertEqual(first["fullPageEvidence"]["rowCount"], 12)
        self.assertNotIn("pageEvidence", first)
        with self.assertRaises(AiError):
            evidence.chunk(run_id, "sales", {"sequence": "1", "rowOffset": "99"}, self.admin)
        with self.assertRaises(AiError):
            evidence.create({**body, "collectionMode": "standard"}, self.admin)

    def test_owner_role_plan_identity_and_input_payload_protection(self):
        run_id = evidence.create(self.body, self.admin)["item"]["id"]
        other = self.user("other-admin@example.invalid", "admin", None)
        for principal in (other, self.viewer, self.owner):
            with self.assertRaises(AiError):
                evidence.get_run(run_id, principal)
        changed = deepcopy(self.body)
        changed["sources"][0]["query"]["shop"] = "另店"
        with self.assertRaises(AiError):
            evidence.create(changed, self.admin)
        with self.assertRaises(AiError):
            evidence.collect(run_id, {"sourceKey": "sales", "expectedVersion": 1, "page": {}}, self.admin, "injection")
        with self.assertRaises(AiError):
            evidence.finish(run_id, {"expectedVersion": 1, "action": []}, self.admin)

    def test_cancellation_during_source_read_cannot_append_late_page(self):
        run_id = evidence.create(self.body, self.admin)["item"]["id"]
        def cancel_before_return(*args, **kwargs):
            result = self.execute(*args, **kwargs)
            if args[0] != "get_data_freshness":
                evidence.finish(run_id, {"expectedVersion": 1, "action": "cancel"}, self.admin)
            return result
        with patch("ai_assistant.transport.catalog", return_value=self.catalog), patch("ai_assistant.transport.execute_tool", side_effect=cancel_before_return), self.assertRaises(AiError):
            evidence.collect(run_id, {"sourceKey": "sales", "expectedVersion": 1}, self.admin, "late")
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.count(), 0)
        self.assertEqual(evidence.get_run(run_id, self.admin).status, "cancelled")

    def test_api_audit_read_write_routes_and_no_paid_model(self):
        response = self.call("/api/ai/business-evidence", self.body, principal=self.admin)
        self.assertEqual(response.status_code, 200, response.content)
        run_id = response.json()["item"]["id"]
        with override_settings(DJANGO_PROCESS_ROLE="ai_reader"), patch("ai_assistant.views.authority"):
            result = self.call(f"/api/ai/business-evidence/{run_id}", principal=self.admin, method="GET")
            self.assertEqual(result.status_code, 200, result.content)
            denied = self.call(f"/api/ai/business-evidence/{run_id}/collect", {"sourceKey": "sales", "expectedVersion": 1}, principal=self.admin)
            self.assertEqual(denied.status_code, 403)
        from .control_models import AiMutationAudit
        self.assertTrue(AiMutationAudit.objects.filter(action="POST /api/ai/business-evidence").exists())
        self.assertEqual(m.AiAgentProviderDispatches.objects.count(), 0)

    def test_collect_audit_failure_rolls_back_page_and_checkpoint(self):
        run_id = evidence.create(self.body, self.admin)["item"]["id"]
        with patch("ai_assistant.transport.catalog", return_value=self.catalog), patch("ai_assistant.transport.execute_tool", side_effect=self.execute), patch("ai_assistant.views.finish", side_effect=AiError("audit unavailable", "service_unavailable", 503)):
            failed = self.call(f"/api/ai/business-evidence/{run_id}/collect", {"sourceKey": "sales", "expectedVersion": 1}, principal=self.admin)
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(m.AiBusinessEvidenceChunk.objects.count(), 0)
        self.assertEqual(evidence.get_run(run_id, self.admin).version, 1)

    def test_postgresql_immutable_chunks_and_terminal_runs(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL trigger contract")
        run_id = evidence.create(self.body, self.admin)["item"]["id"]
        self.collect(run_id, 1)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessEvidenceChunk.objects.filter(run_id=run_id).update(payload_json="{}")
        evidence.finish(run_id, {"expectedVersion": 2, "action": "cancel"}, self.admin)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessEvidenceRun.objects.filter(pk=run_id).update(status="collecting", version=4)

    def test_mapping_never_fans_out_ambiguous_or_missing_online_codes(self):
        sales = read_page(self.admin, {**self.query, "operation": "analysis_records", "limit": 100})
        rows = [{"rowId": str(i), "platform": "京东", "shopName": "京东一店", "skuId": sku, "spuId": "SPU", "dimensions": {"merchantCode": "M1"}, "metrics": {}} for i, sku in enumerate(("SKU-A", "SKU-B"), 1)]
        master = {"schemaVersion": SCHEMA_VERSION, "sourceRef": "master-fixed", "sourceDataset": "product_master",
            "filters": {"platform": "京东", "shop": "京东一店"}, "items": rows,
            "control": {"rowCount": 2, "typedTotals": {}}, "pageEvidence": {"rowCount": 2, "sha256": page_digest(rows)},
            "pagination": {"hasMore": False, "nextCursor": None}}
        result = product_reconciliation([sales], [master])
        self.assertEqual(result["coverage"]["ambiguous"], 12)
        self.assertEqual(result["totals"]["netSalesCents"], 120000)
        self.assertIsNone(result["groups"][0]["skuId"])

    def test_sealed_cross_domain_mapping_reads_only_persisted_evidence(self):
        from netshop.models import NetshopImportBatch, NetshopRow
        from netshop.analysis import read_page as netshop_page, validate_request
        from sales.models import SalesOrderLine
        from django.http import QueryDict
        from urllib.parse import urlencode
        SalesOrderLine.objects.filter(pk=1).update(online_spec_code="")
        with versioned_netshop_facts():
            NetshopImportBatch.objects.create(id="master", source="jd_product_master", dataset="product_master", platform="京东", shop_name="京东一店",
                file_name="synthetic.xlsx", file_size_bytes=1, file_hash="a"*64, raw_file_hash="a"*64, content_hash="a"*64, scope_key="a"*64,
                status="completed", snapshot_date="2026-08-01", created_at="2026-08-01", completed_at="2026-08-01")
            NetshopRow.objects.create(source_row_key="master-1", source_row_hash="b"*64, first_import_batch_id="master", last_import_batch_id="master",
                source_row_number=1, source="jd_product_master", dataset="product_master", platform="京东", shop_name="京东一店", sku_id="SKU1", spu_id="SPU1",
                snapshot_date="2026-08-01", raw_json={"商家编码": "M1"}, created_at="2026-08-01", updated_at="2026-08-01")
        body = deepcopy(self.body)
        body["sources"].append({"key": "master", "domain": "netshop", "query": {**{k: v for k, v in self.query.items() if k != "channel"}, "dataset": "master"}})
        run_id = evidence.create(body, self.admin)["item"]["id"]
        self.collect(run_id, 1)
        self.collect(run_id, 2)
        def execute(name, args, principal, **kwargs):
            if name != "get_netshop_analysis_records":
                return self.execute(name, args, principal, **kwargs)
            return {"toolName": name, "ok": True, "auditStatus": "recorded", "data": netshop_page(*validate_request(QueryDict(urlencode(args))))}
        with patch("ai_assistant.transport.catalog", return_value=self.catalog), patch("ai_assistant.transport.execute_tool", side_effect=execute):
            evidence.collect(run_id, {"sourceKey": "master", "expectedVersion": 3}, self.admin, "master-read")
        evidence.finish(run_id, {"expectedVersion": 4, "action": "seal"}, self.admin)
        with patch("ai_assistant.transport.execute_tool") as remote:
            result = evidence.reconcile_products(run_id, {"sales": "sales", "master": "master"}, self.admin)
            remote.assert_not_called()
        self.assertEqual(result["coverage"], {"matched": 11, "ambiguous": 0, "unmatched": 1})
        self.assertEqual(result["totals"]["netSalesCents"], 120000)
        self.assertEqual(sum(g["metrics"]["netSalesCents"] for g in result["groups"]), 120000)

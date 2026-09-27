"""Real isolated source readers -> sealed 19-source catalog -> complete volumes.

Only transport and already-written report prose are mocked. No live source or
provider is called; the report is explicitly a draft synthetic acceptance case.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import zipfile
from unittest.mock import patch
from urllib.parse import urlencode

from django.http import QueryDict
from django.test import TestCase, override_settings
from market.analysis import read_page as market_page
from market.models import MarketRankingEntry
from netshop.analysis import SOURCES, read_page as netshop_page, validate_request
from netshop.models import NetshopRow
from sales.analysis import read_page as sales_page
from sales.tests.factories import make_line
from business_analysis.report_files import MAX_TABLES
from business_analysis.test_report_files import ReportData
from business_analysis.volume_files import VolumeStreams
from . import business_evidence as evidence, business_export, business_reports as reports, models as m, tests as fixtures
from .test_business_evidence import versioned_netshop_facts
from .policy import AiError


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessExportCatalogAcceptanceTests(TestCase):
    user = fixtures.AiDomainTests.user

    def setUp(self):
        fixtures.AiDomainTests.setUp(self)
        self.admin = self.user("volume-acceptance@example.invalid", "admin", None)
        self.shop, self.channel = "合成完整店", "京东-合成完整店"
        days = ("2026-08-01", "2026-07-31", "2025-08-01")
        with versioned_netshop_facts():
            for index, day in enumerate(days, 1):
                make_line(index, "volume-erp-"+str(index), shop_name=self.shop, channel=self.channel,
                    ship_time=day+" 10:00:00", line_ship_time=day+" 10:00:00", sales_time=day+" 08:00:00",
                    online_spec_code="CODE-1", category="饮水机").save()
                for dataset in ("promotion", "sku", "spu", "b2b"):
                    source, kind = SOURCES[dataset]["京东"]
                    NetshopRow.objects.create(source_row_key=f"volume-{dataset}-{index}", source_row_hash=hashlib.sha256(f"{dataset}-{index}".encode()).hexdigest(),
                        first_import_batch_id="synthetic", last_import_batch_id="synthetic", source_row_number=index,
                        source=source, dataset=kind, platform="京东", shop_name=self.shop, business_date=day,
                        sku_id="SKU-1" if dataset != "spu" else "", spu_id="SPU-1", category="饮水机", product_name="合成产品",
                        spend_cents=1000*index, net_transaction_amount_cents=5000*index, clicks=10*index, impressions=100*index, net_orders=index,
                        transaction_amount_cents=4000*index, transaction_quantity=index, visitors=20*index, page_views=30*index, transaction_orders=index,
                        metrics_json={"spendCents": 1000*index, "netTransactionAmountCents": 5000*index, "clicks": 10*index, "impressions": 100*index,
                            "netOrders": index, "transactionAmountCents": 4000*index, "transactionQuantity": index, "visitors": 20*index, "pageViews": 30*index, "transactionOrders": index},
                        raw_json={"关键词": "合成关键词", "搜索词": "合成搜索词"})
                MarketRankingEntry.objects.create(natural_key="volume-market-"+str(index), source_row_number=index,
                    period_start=day, period_end=day, category="饮水机", scope="POP", ranking_dimension="SKU", price_band_filter="全部",
                    sku_code="MARKET-1", product_name="合成市场产品", brand="合成品牌", rank=1,
                    gmv_low_cents=2000*index, gmv_high_cents=3000*index, quantity_low=index, quantity_high=2*index,
                    last_import_batch_id="synthetic")
        common = {"platform": "京东", "startDate": days[0], "endDate": days[0]}
        self.sources = []
        for window in ("current", "previous", "yearAgo"):
            for dataset in ("promotion", "sku", "spu", "b2b"):
                self.sources.append({"key": dataset+"-"+window, "domain": "netshop", "query": {**common, "shop": self.shop, "dataset": dataset, "window": window}})
            self.sources.append({"key": "erp-"+window, "domain": "sales", "query": {**common, "shop": self.shop, "channel": self.channel, "window": window}})
            self.sources.append({"key": "market-"+window, "domain": "market", "query": {**common, "category": "饮水机", "scope": "POP", "rankingDimension": "SKU", "priceBandFilter": "全部", "window": window}})
        self.sources.append({"key": "master", "domain": "netshop", "query": {**common, "shop": self.shop, "dataset": "master", "window": "current"}})
        body = {"schemaVersion": "business-evidence-v2", "clientRequestId": "complete-catalog", "collectionMode": "bulk", "sources": self.sources}
        if hasattr(self, "planned_evidence_body"):
            body = self.planned_evidence_body(common)
            self.sources = body["sources"]
        self.run_id = evidence.create(body, self.admin)["item"]["id"]
        tools = [{**fixtures.CATALOG[0], "name": name} for name in ("get_data_freshness", "get_business_source_page")]
        def transport(name, args, principal, **kwargs):
            self.assertEqual(kwargs["surface"], "business_collection")
            if name == "get_data_freshness":
                data = {"dataCutoffDate": days[0]}
            else:
                domain = args["domain"]
                query = {key: value for key, value in args.items() if key != "domain"}
                if domain == "netshop":
                    data = netshop_page(*validate_request(QueryDict(urlencode(query))))
                else:
                    data = (market_page if domain == "market" else sales_page)(principal, {"operation": "analysis_records", **query})
            return {"toolName": name, "ok": True, "auditStatus": "recorded", "data": data}
        version = 1
        with patch("ai_assistant.transport.catalog", return_value=tools), patch("ai_assistant.transport.execute_tool", side_effect=transport):
            for source in self.sources:
                result = evidence.collect(self.run_id, {"sourceKey": source["key"], "expectedVersion": version}, self.admin, "synthetic-volume")
                version = result["item"]["version"]
                self.assertTrue(result["item"]["sources"][source["key"]]["complete"])
        evidence.finish(self.run_id, {"expectedVersion": version, "action": "seal"}, self.admin)
        if not getattr(self, "skip_draft_report", False):
            created = reports.create({"clientRequestId": "volume-draft", "evidenceRunId": self.run_id, "question": "合成三周期完整来源验收", "dryRun": True}, self.admin)
            self.report = m.AiReportRun.objects.select_related("workflow").get(pk=created["item"]["id"])
        self.content = {"sections": [{"title": "合成范围", "body": "三周期完整来源；主数据为空明确保留，非真实经营结论。"}],
            "diagnosis": {"findings": [{"id": "gap", "kind": "gap", "title": "仍需真实验收", "explanation": "此处只验证文件完整性", "facts": []}]}}

    def test_actual_19_sources_produce_156_tables_and_two_complete_volume_pairs(self):
        with patch("ai_assistant.business_reports.content", return_value=self.content), patch("ai_assistant.transport.execute_tool") as remote, patch("ai_assistant.provider.turn") as provider:
            with business_export.prepare_volumes(self.report, self.admin, draft=True) as prepared:
                self.assertEqual(prepared.plan["sourceTableCount"], 156)
                self.assertGreater(prepared.plan["sourceTableCount"], MAX_TABLES)
                self.assertEqual(prepared.plan["volumeCount"], 2)
                outputs = [VolumeStreams(io.BytesIO(), io.BytesIO()) for _ in range(2)]
                manifest = business_export.build_volumes(prepared, outputs)
            remote.assert_not_called()
            provider.assert_not_called()
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["sourceTableCount"], 156)
        self.assertEqual(sum(v["rowCount"] for v in manifest["volumes"]), manifest["totalRows"])
        self.assertEqual({t["key"] for t in manifest["tables"] if t["key"].startswith("raw-")}, {"raw-"+s["key"] for s in self.sources})
        self.assertEqual(next(t for t in manifest["tables"] if t["key"] == "raw-master")["rowCount"], 0)
        combined = []
        for output, volume in zip(outputs, manifest["volumes"]):
            html = ReportData(output.html.getvalue().decode()).value
            combined.extend(html["tables"])
            for kind in ("html", "xlsx"):
                raw = getattr(output, kind).getvalue()
                self.assertEqual(volume["files"][kind]["bytes"], len(raw))
                self.assertEqual(volume["files"][kind]["sha256"], hashlib.sha256(raw).hexdigest())
            with zipfile.ZipFile(output.xlsx) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(len(json.loads(archive.read("teruisi-manifest.json"))["tables"]), len(volume["tables"]))
        erp = next(t for t in combined if t["title"] == "erp-current_店铺_环比")
        columns = [c["key"] for c in erp["columns"]]
        self.assertEqual(erp["rows"][0][columns.index("/comparisons/netSalesCents/difference")], 0)
        market = next(t for t in combined if t["title"] == "来源_market-current")
        self.assertEqual(market["rows"][0][[c["key"] for c in market["columns"]].index("/shopName")], "")
        if os.environ.get("TERUISI_SYNTHETIC_VOLUME_OUTPUT") == "1":
            directory = Path(__file__).resolve().parents[2] / ".runtime" / "batch19-synthetic-volumes"
            directory.mkdir(parents=True, exist_ok=True)
            for index, output in enumerate(outputs, 1):
                for kind in ("html", "xlsx"):
                    (directory / f"volume-{index}.{kind}").write_bytes(getattr(output, kind).getvalue())
            (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        with self.assertRaises(AiError):
            business_export.build(self.report, self.admin, io.BytesIO(), io.BytesIO(), draft=True)

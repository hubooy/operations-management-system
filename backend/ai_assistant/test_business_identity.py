"""Real persisted v2 source pairs, with no source reads after sealing."""
from copy import deepcopy
from unittest.mock import patch
from urllib.parse import urlencode

from django.db import connection
from django.http import QueryDict
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from netshop.analysis import read_page as master_page, validate_request
from netshop.models import NetshopImportBatch, NetshopRow
from sales.models import SalesOrderLine, SalesDataRevision
from sales.analysis import read_page as sales_page
from sales.analysis_continuation import read_page as sales_continuation_page
from sales.tests.factories import signed_headers, TEST_SECRET, make_line
from . import business_evidence as evidence, business_identity as identity, tests as fixtures
from . import test_business_evidence as evidence_fixtures
from .test_business_evidence import versioned_netshop_facts
from .policy import AiError, digest


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessIdentityTests(TestCase):
    user = fixtures.AiDomainTests.user
    call = fixtures.AiDomainTests.call

    def setUp(self):
        evidence_fixtures.BusinessEvidenceTests.setUp(self)
        for domain in ("sales", "erp"):
            SalesDataRevision.objects.get_or_create(domain=domain, defaults={"revision": 1, "source_digest": "a"*64})
        SalesOrderLine.objects.filter(pk=1).update(online_spec_code="")
        with versioned_netshop_facts():
            NetshopImportBatch.objects.create(id="master", source="jd_product_master", dataset="product_master", platform="京东", shop_name="京东一店",
                file_name="synthetic.xlsx", file_size_bytes=1, file_hash="a"*64, raw_file_hash="a"*64, content_hash="a"*64, scope_key="a"*64,
                status="completed", snapshot_date="2026-08-01", created_at="2026-08-01", completed_at="2026-08-01")
            NetshopRow.objects.create(source_row_key="master-1", source_row_hash="b"*64, first_import_batch_id="master", last_import_batch_id="master",
                source_row_number=1, source="jd_product_master", dataset="product_master", platform="京东", shop_name="京东一店", sku_id="SKU1", spu_id="SPU1",
                snapshot_date="2026-08-01", raw_json={"商家编码": "M1"}, created_at="2026-08-01", updated_at="2026-08-01")
        body = deepcopy(self.body)
        body.update(schemaVersion="business-evidence-v2", collectionMode="bulk")
        body["sources"].append({"key": "master", "domain": "netshop", "query": {**{k:v for k,v in self.query.items() if k != "channel"}, "dataset": "master"}})
        self.evidence_body = body
        self.run_id = evidence.create(body, self.admin)["item"]["id"]
        def execute(name, args, actor, **kwargs):
            if name == "get_data_freshness": data = {"dataCutoffDate": "2026-08-01"}
            elif name == "get_business_sales_continuation_page":
                self.assertNotIn("domain", args)
                data = sales_continuation_page(actor, QueryDict(urlencode(args)))
            else:
                values = {k:v for k,v in args.items() if k != "domain"}
                data = sales_page(actor, {"operation": "analysis_records", **values}) if args["domain"] == "sales" else master_page(*validate_request(QueryDict(urlencode(values))))
            return {"toolName": name, "ok": True, "auditStatus": "recorded", "data": data}
        tools = [fixtures.CATALOG[0], *[{**fixtures.CATALOG[0], "name": name} for name in (
            "get_business_source_page", "get_business_sales_continuation_page")]]
        self.source_tools, self.source_execute = tools, execute
        with patch("ai_assistant.transport.catalog", return_value=tools), patch("ai_assistant.transport.execute_tool", side_effect=execute):
            evidence.collect(self.run_id, {"sourceKey": "sales", "expectedVersion": 1}, self.admin, "sales-seed")
            evidence.collect(self.run_id, {"sourceKey": "master", "expectedVersion": 2}, self.admin, "master-seed")
        evidence.finish(self.run_id, {"expectedVersion": 3, "action": "seal"}, self.admin)

    def page(self, actor=None, **values):
        return identity.page(self.run_id, {"salesKey": "sales", "masterKey": "master", **values}, actor or self.admin)

    def test_complete_page_matches_legacy_totals_and_binds_both_sources(self):
        original = evidence.reconcile_products(self.run_id, {"sales": "sales", "master": "master"}, self.admin)
        with patch("ai_assistant.provider.turn") as model, patch("ai_assistant.transport.execute_tool") as remote, CaptureQueriesContext(connection) as queries:
            first = self.page()
        self.assertEqual(first["mapping"]["coverage"], {"matched": 11, "ambiguous": 0, "unmatched": 1})
        for key in ("totals", "coverage", "rowCount", "sources", "mappingBasis", "limitations"):
            self.assertEqual(first["mapping"][key], original[key])
        self.assertEqual(first["bindingDigest"], digest(first["binding"]))
        self.assertEqual(first["binding"]["sales"]["sourceRef"], original["sources"]["sales"]["sourceRef"])
        self.assertEqual(first["binding"]["master"]["sourceRef"], original["sources"]["master"]["sourceRef"])
        self.assertEqual([r["rowIndex"] for r in first["mapping"]["rows"]], [0,1])
        model.assert_not_called(); remote.assert_not_called()
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
        self.assertFalse(any(any(table in q["sql"].lower() for table in ("netshop_rows", "sales_order_lines")) for q in queries))
        self.assertEqual(self.page()["mapping"], first["mapping"])
        self.assertEqual(self.page(offset="2")["mapping"]["rows"], [])

    def test_exact_pair_query_owner_and_complete_scan_failure_are_closed(self):
        for params in ({"salesKey":"master"}, {"masterKey":"sales"}, {"masterKey":"missing"}, {"offset":"01"},
                       {"offset":"-1"}, {"offset":"250001"}, {"limit":"10"}, {"unknown":"value"}):
            with self.subTest(params=params), self.assertRaises(AiError): self.page(**params)
        for actor in (self.viewer, self.user("mapping-other@example.invalid", "admin", None)):
            with self.assertRaises(AiError): self.page(actor)
        with patch("ai_assistant.business_sealed.Reader.pages", side_effect=AiError("late broken fact")), self.assertRaises(AiError): self.page()
        with patch.object(identity, "_current", side_effect=[None, None, AiError("late revoked principal")]), self.assertRaises(AiError): self.page()

    def test_context_rejects_reads_after_close_and_scope_changes(self):
        with identity.reconciled(self.run_id, "sales", "master", self.admin) as (result, binding):
            self.assertEqual(len(list(result.scan())), 2)
            self.assertEqual(binding["evidenceRunId"], self.run_id)
        from business_analysis.contracts import AnalysisContractError
        with self.assertRaises(AnalysisContractError): result.page()
        from access_control.models import AppUser
        AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        with self.assertRaises(AiError): self.page()

    def test_mapping_route_is_reader_only_exact_and_preserves_legacy_endpoint(self):
        root = f"/api/ai/business-evidence/{self.run_id}"
        def get(suffix, role="ai_reader", actor=None):
            actor = actor or self.admin
            url = root+suffix
            with patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}), override_settings(
                    DJANGO_INTERNAL_SECRET=TEST_SECRET, DJANGO_PROCESS_ROLE=role), patch("ai_assistant.views.authority"):
                return self.client.get(url, headers=signed_headers(url, email=actor.email, role=actor.role, scope=actor.scope))
        query = "/mapping-v2?salesKey=sales&masterKey=master&offset=0&limit=20"
        response = get(query)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertLessEqual(len(response.content), identity.MAX_RESPONSE_BYTES)
        self.assertEqual(response.json()["mapping"]["totals"]["netSalesCents"], 120000)
        old = get("/mapping?sales=sales&master=master")
        self.assertEqual(old.status_code, 200, old.content)
        self.assertEqual(old.json()["schemaVersion"], "business-product-mapping-v1")
        self.assertEqual(get(query, role="ai_writer").status_code, 403)
        self.assertIn(get(query, actor=self.viewer).status_code, (403,404))
        self.assertEqual(get(query+"&offset=1").status_code, 400)
        self.assertEqual(get(query+"&extra=1").status_code, 400)
        self.assertEqual(self.call(root+"/mapping-v2", {}, self.admin).status_code, 405)

    def test_real_5001_sales_rows_exceed_old_limit_and_fully_map_from_saved_pages(self):
        SalesOrderLine.objects.bulk_create([make_line(i, f"wide-mapping-{i}", channel=self.query["channel"], online_spec_code="M1")
            for i in range(13,5002)], batch_size=500)
        body = deepcopy(self.evidence_body); body["clientRequestId"] = "wide-mapping-evidence"
        run_id = evidence.create(body, self.admin)["item"]["id"]
        pages = 0
        with patch("ai_assistant.transport.catalog", return_value=self.source_tools), patch("ai_assistant.transport.execute_tool", side_effect=self.source_execute):
            for key in ("sales", "master"):
                while True:
                    current = evidence.get_run(run_id, self.admin)
                    result = evidence.collect(run_id, {"sourceKey": key, "expectedVersion": current.version}, self.admin, "wide-seed")
                    pages += 1
                    if result["item"]["sources"][key]["complete"]: break
                    self.assertLess(pages, 60)
        current = evidence.get_run(run_id, self.admin)
        evidence.finish(run_id, {"expectedVersion": current.version, "action": "seal"}, self.admin)
        self.assertEqual(pages, 52)
        with self.assertRaises(AiError) as old:
            evidence.reconcile_products(run_id, {"sales":"sales", "master":"master"}, self.admin)
        self.assertEqual(old.exception.status, 413)
        with patch("ai_assistant.transport.execute_tool") as remote, patch("ai_assistant.provider.turn") as model:
            result = identity.page(run_id, {"salesKey":"sales", "masterKey":"master"}, self.admin)
        remote.assert_not_called(); model.assert_not_called()
        self.assertEqual(result["mapping"]["rowCount"], 5001)
        self.assertEqual(result["mapping"]["coverage"], {"matched":5000, "ambiguous":0, "unmatched":1})
        self.assertEqual(result["mapping"]["totals"]["netSalesCents"], 50010000)
        self.assertEqual(sum(r["metrics"]["netSalesCents"] for r in result["mapping"]["rows"]), 50010000)

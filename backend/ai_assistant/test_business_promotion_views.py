"""Real JD owning reader -> sealed pages -> internal report-bound promotion views."""
from contextlib import contextmanager
from copy import deepcopy
import re
import secrets
from unittest.mock import patch

from django import test as djtest
from django.db import DatabaseError, connection, transaction
from django.test.utils import CaptureQueriesContext
from netshop.models import NetshopRow

from business_analysis.contracts import AnalysisContractError
from . import business_evidence as evidence, business_promotion_views as service
from . import test_business_integrated_guard as fixtures
from .test_business_evidence import versioned_netshop_facts
from .policy import AiError, canonical, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessPromotionViewTests(djtest.TransactionTestCase):
    user = fixtures.BusinessIntegratedGuardTests.user
    call = fixtures.BusinessIntegratedGuardTests.call
    bundle = fixtures.BusinessIntegratedGuardTests.bundle
    input_for = fixtures.BusinessIntegratedGuardTests.input_for
    insert = fixtures.BusinessIntegratedGuardTests.insert
    seed = fixtures.BusinessIntegratedGuardTests.seed

    def collect_body(self, body):
        run = evidence.create(body, self.admin)["item"]
        with patch("ai_assistant.transport.catalog", return_value=self.source_tools), patch(
                "ai_assistant.transport.execute_tool", side_effect=self.source_execute):
            for source in body["sources"]:
                while True:
                    row = evidence.get_run(run["id"], self.admin)
                    result = evidence.collect(row.id, {"sourceKey": source["key"], "expectedVersion": row.version},
                        self.admin, "promotion-owning-test")["item"]
                    if result["sources"][source["key"]]["complete"]:
                        break
        row = evidence.get_run(run["id"], self.admin)
        evidence.finish(row.id, {"expectedVersion": row.version, "action": "seal"}, self.admin)
        return evidence.get_run(row.id, self.admin)

    def ad(self, number, plan, unit, match, amount, *, date="2026-08-01", shop=None):
        if not connection.in_atomic_block:
            with versioned_netshop_facts():
                return self.ad(number, plan, unit, match, amount,
                    date=date, shop=shop)
        values = dict(source_row_hash=digest([number, plan, unit, match, amount, date]),
            source_row_number=number+1, first_import_batch_id="fixture", last_import_batch_id="fixture",
            source="jd_promotion", dataset="ad", platform="京东", shop_name=shop or self.query["shop"],
            business_date=date, sku_id="S"+str(number), spu_id="P1", spend_cents=amount,
            net_transaction_amount_cents=amount*5, clicks=10, impressions=100, net_orders=2,
            metrics_json={"spendCents":amount, "netTransactionAmountCents":amount*5,
                "clicks":10, "impressions":100, "netOrders":2},
            raw_json={"计划ID":plan, "单元ID":unit, "匹配类型":match, "计划名称":"合成计划"})
        NetshopRow.objects.update_or_create(source_row_key="integrated-ad-"+str(number), defaults=values)

    def setUp(self):
        fixtures.BusinessIntegratedGuardTests.setUp(self)
        with versioned_netshop_facts():
            for args in ((0,"P1","U1","精确",100),(1,"P1","U1","广泛",200),
                    (2,"P1","U2",None,300),(3,"P2","U1",None,400),(4,None,None,None,50)):
                self.ad(*args)
            self.ad(5,"P1","U1","精确",50,date="2026-07-31")
            self.ad(6,"P1","U1","精确",900,shop="合成其他店")
        body = deepcopy(self.evidence_body)
        body.update(clientRequestId="promotion-owning-fixed", sources=deepcopy(self.sources))
        current = next(source for source in body["sources"] if source["key"] == "ads")
        body["sources"].extend([
            {**deepcopy(current), "key":"ads-previous", "query":{**current["query"], "window":"previous"}},
            {**deepcopy(current), "key":"ads-other", "query":{**current["query"], "shop":"合成其他店"}},
        ])
        self.parent = self.collect_body(body)
        self.report, _ = self.seed()
        self.fixed_body = body

    def page(self, **params):
        return service.page(self.report.id, {"sourceKey":"ads", "view":"unit", **params}, self.admin)

    def test_actual_native_facts_three_views_compare_and_missing_bucket_conserve(self):
        for view, amounts in (("plan",[50,400,600]),("unit",[50,300,300,400]),
                ("unit_match",[50,100,200,300,400])):
            result = self.page(view=view, baselineKey="ads-previous")
            rows = result["table"]["rows"]
            self.assertEqual(sorted(row["metrics"]["spendCents"]["value"] for row in rows), amounts)
            self.assertEqual(sum(row["currentRowCount"] for row in rows),5)
            self.assertEqual(sum(row["metrics"]["spendCents"]["value"] for row in rows),1050)
            self.assertTrue(result["authority"]["completeSourceTraversalForSelectedSources"])
            self.assertFalse(result["table"]["authorityVerified"])
            self.assertEqual(result["binding"]["reportBinding"]["reportId"],self.report.id)
            self.assertTrue(result["table"]["dateCoverageComparable"])
            for row in rows:
                if not row["identityQualified"]:
                    self.assertEqual(row["comparisons"]["spendCents"]["status"],"unavailable")
        result = self.page(view="plan",baselineKey="ads-previous")
        matched = next(row for row in result["table"]["rows"] if row["entity"]["planId"] == "P1")
        self.assertEqual(matched["comparisons"]["spendCents"]["difference"],550)

    def test_pages_read_both_complete_sources_once_no_business_io_or_models(self):
        calls, finished = [], []
        original = service.report_binding.Reader.pages
        def tracked(reader,key,*args,**kwargs):
            calls.append(key)
            yield from original(reader,key,*args,**kwargs)
            finished.append(key)
        with patch.object(service.report_binding.Reader,"pages",tracked), patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote, CaptureQueriesContext(connection) as queries:
            with service.table(self.report.id,"ads","unit",self.admin,baseline_key="ads-previous") as (opened,binding):
                self.assertEqual(calls,["ads","ads-previous"])
                self.assertEqual(finished,calls)
                opened.page(); list(opened.scan()); opened.page()
                self.assertEqual(calls,["ads","ads-previous"])
                self.assertEqual(binding["view"],"unit")
        model.assert_not_called(); remote.assert_not_called()
        self.assertFalse(any("netshop_rows" in query["sql"].lower() or "sales_order_lines" in query["sql"].lower() for query in queries))
        self.assertFalse(any(query["sql"].lstrip().upper().startswith(("INSERT","UPDATE","DELETE")) for query in queries))
        with self.assertRaises(AnalysisContractError): opened.header()

    def test_exact_row_view_source_binding_and_wrapper_digests(self):
        result = self.page()
        row = result["table"]["rows"][0]
        found = service.read_row(self.report.id,"ads","unit",row["rowIndex"],row["id"],self.admin)
        self.assertEqual(found["row"],row)
        self.assertEqual(found["bindingDigest"],result["bindingDigest"])
        self.assertEqual(found["responseDigest"],digest({k:v for k,v in found.items() if k != "responseDigest"}))
        for view, baseline in (("plan",None),("unit","ads-previous")):
            with self.assertRaises(AiError):
                service.read_row(self.report.id,"ads",view,row["rowIndex"],row["id"],self.admin,baseline_key=baseline)
        with self.assertRaises(AiError): service.read_row(self.report.id,"ads","unit",0,"0"*64,self.admin)
        with self.assertRaises(AiError): service.read_row(self.report.id,"ads","unit",True,row["id"],self.admin)

    def test_real_reader_grants_read_sealed_views_without_business_queries_or_writes(self):
        from .database_contract import READ_TABLES, provision

        # Facts and the immutable report are already committed by setUp. Apply
        # the real role contract; no Reader, binding or calculation is mocked.
        expected = {view: self.page(view=view, baselineKey="ads-previous")
            for view in ("plan", "unit", "unit_match")}
        connection.ensure_connection()
        provision(connection.connection, secrets.token_hex(32), secrets.token_hex(32))
        with transaction.atomic(), self.settings(DJANGO_PROCESS_ROLE="ai_reader"):
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE teruisi_ai_reader")
                cursor.execute("SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user")
                self.assertEqual(cursor.fetchone(), ("teruisi_ai_reader", False, False))
            # SET ROLE intentionally does not inherit a login's read-only GUC:
            # successful reads and denied writes must follow actual grants.
            with patch("ai_assistant.provider.turn") as model, patch(
                    "ai_assistant.transport.execute_tool") as remote, CaptureQueriesContext(connection) as queries:
                for view, wanted in expected.items():
                    actual = self.page(view=view, baselineKey="ads-previous")
                    self.assertEqual(canonical(actual), canonical(wanted))
                    selected = actual["table"]["rows"][0]
                    found = service.read_row(self.report.id, "ads", view,
                        selected["rowIndex"], selected["id"], self.admin, baseline_key="ads-previous")
                    self.assertEqual(found["row"], selected)
                    self.assertEqual(found["bindingDigest"], actual["bindingDigest"])
            model.assert_not_called(); remote.assert_not_called()
            self.assertTrue(queries.captured_queries)
            accessed = set()
            for query in queries:
                sql = query["sql"]
                # Django's streaming iterator declares a SELECT cursor; this
                # remains a read even though the SQL starts with DECLARE.
                self.assertRegex(sql.lstrip(), r'(?i)^(?:SELECT\b|DECLARE\s+"_django_curs_[^"]+"\s+NO SCROLL CURSOR FOR SELECT\b)')
                self.assertNotRegex(sql, r"(?i)\bFOR\s+(?:UPDATE|SHARE|KEY\s+SHARE|NO\s+KEY\s+UPDATE)\b")
                accessed.update(re.findall(
                    r'(?i)\b(?:FROM|JOIN)\s+(?:"?public"?\.)?"?([a-z_][a-z_0-9]*)"?', sql))
            self.assertTrue(accessed)
            self.assertLessEqual(accessed, set(READ_TABLES))
            self.assertIn("ai_business_evidence_chunks", accessed)
            # Probe physical permissions separately from the captured service
            # reads; failed statements are isolated by savepoints.
            for sql in (
                "SELECT * FROM netshop_rows LIMIT 0",
                "SELECT * FROM sales_order_lines LIMIT 0",
                "UPDATE ai_report_runs SET snapshot_json=snapshot_json WHERE false",
                "DELETE FROM ai_business_evidence_chunks WHERE false",
                "INSERT INTO ai_business_evidence_chunks SELECT * FROM ai_business_evidence_chunks WHERE false",
            ):
                with self.subTest(sql=sql), self.assertRaisesRegex(DatabaseError, "permission denied"):
                    with transaction.atomic(), connection.cursor() as cursor:
                        cursor.execute(sql)
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user=session_user")
            self.assertTrue(cursor.fetchone()[0])

    def test_owner_scope_invalid_view_source_and_baseline_fail_before_facts(self):
        for actor in (self.viewer,self.user("promotion-other@example.invalid","admin",None)):
            with self.assertRaises(AiError): service.page(self.report.id,{"sourceKey":"ads","view":"plan"},actor)
        from sales.auth import Principal
        actor = Principal(self.admin.email,"scoped","admin",{"shops":["other"]})
        with self.assertRaises(AiError): service.page(self.report.id,{"sourceKey":"ads","view":"plan"},actor)
        def forbidden_facts(*args,**kwargs):
            self.fail("invalid selection must not consume a fact stream")
            yield None
        for params in ({"sourceKey":"sales"},{"sourceKey":"master"},{"sourceKey":"missing"},{"view":"sku"},
                {"baselineKey":"ads-other"},{"baselineKey":"ads"},{"unknown":True},{"offset":True},
                {"offset":1.0},{"offset":"0"},{"offset":250001},{"limit":True},{"limit":10}):
            with self.subTest(params=params), patch.object(service.report_binding.Reader,"pages",forbidden_facts), self.assertRaises(AiError):
                self.page(**params)

    def test_tail_failure_and_late_context_revocation_never_return_authority(self):
        original = service.report_binding.Reader.pages
        def broken(reader,key,*args,**kwargs):
            yield from original(reader,key,*args,**kwargs)
            raise AiError("synthetic late source failure")
        with patch.object(service.report_binding.Reader,"pages",broken), self.assertRaisesRegex(AiError,"late"):
            self.page()
        pure = service.promotion_views.table
        @contextmanager
        def revoked(*args,**kwargs):
            with pure(*args,**kwargs) as result:
                yield result
            from access_control.models import AppUser
            AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        with patch.object(service.promotion_views,"table",revoked), self.assertRaises(AiError):
            self.page()
        with self.assertRaises(AiError): self.page()

    def test_wide_utf8_full_prefix_and_no_dropped_rows(self):
        for index in range(20,43):
            self.ad(index,"中"*180+str(index),"单"*180,"精"*180,index)
        body = deepcopy(self.fixed_body); body["clientRequestId"] = "promotion-wide"
        self.parent = self.collect_body(body); self.report,_ = self.seed()
        actual, offset = [],0
        with patch.object(service,"MAX_RESPONSE_BYTES",16000):
            while True:
                result = self.page(view="unit_match",offset=offset)
                self.assertLessEqual(len(canonical(result).encode("utf-8")),16000)
                part = result["table"]
                self.assertEqual(part["pageDigest"],digest({k:v for k,v in part.items() if k != "pageDigest"}))
                actual.extend(part["rows"])
                offset = part["pagination"]["nextOffset"]
                if offset is None: break
        with service.table(self.report.id,"ads","unit_match",self.admin) as (opened,_):
            self.assertEqual(actual,list(opened.scan()))
        self.assertEqual(len(actual),28)

    def test_sealed_binding_snapshot_or_page_changes_are_rejected(self):
        load = service.report_binding._load
        calls = []
        def altered(report_id,principal):
            result = load(report_id,principal)
            calls.append(report_id)
            if len(calls) == 1:
                result[0]["snapshotDigest"] = "0"*64
            return result
        with patch.object(service.report_binding,"_load",altered), self.assertRaises(AiError):
            self.page()
        original = service.report_binding.Reader.pages
        def damaged(reader,key,*args,**kwargs):
            for page in original(reader,key,*args,**kwargs):
                if key == "ads" and page["items"]:
                    page["items"][0]["metrics"]["spendCents"] += 1
                yield page
        with patch.object(service.report_binding.Reader,"pages",damaged), self.assertRaises(AiError):
            self.page()

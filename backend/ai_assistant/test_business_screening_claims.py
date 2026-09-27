"""Real sealed facts -> published role packages -> internal numeric references.

Tests require root's isolated PostgreSQL harness; this module starts no service
and never calls a model. Private mutations exercise data integrity, not defense
against arbitrary Python code execution in the service process.
"""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from types import SimpleNamespace
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from netshop.models import NetshopRow
from sales.models import SalesOrderLine
from sales.tests.factories import make_line

from business_analysis import mapping_plan, screening_package as pure
from . import business_diagnostic_screening as screening, business_screening_store as store
from . import business_screening_packages as packages, business_screening_claims as service
from . import test_business_diagnostic_screening as fixtures
from .test_business_evidence import versioned_netshop_facts
from .policy import AiError, canonical, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",DJANGO_ENVIRONMENT="test")
class ScreeningClaimsTests(djtest.TransactionTestCase):
    user = fixtures.DiagnosticScreeningTests.user
    call = fixtures.DiagnosticScreeningTests.call
    collect_body = fixtures.DiagnosticScreeningTests.collect_body
    bundle = fixtures.DiagnosticScreeningTests.bundle
    input_for = fixtures.DiagnosticScreeningTests.input_for
    insert = fixtures.DiagnosticScreeningTests.insert
    seed = fixtures.DiagnosticScreeningTests.seed

    def setUp(self):
        fixtures.DiagnosticScreeningTests.setUp(self)
        SalesOrderLine.objects.filter(pk=1).update(allocated_amount_cents=-200,cost_amount_cents=0)
        SalesOrderLine.objects.filter(pk=2).update(fee_allocation_cents=200)
        for key,amount in ((99,-50),(100,200000)):
            make_line(key,"claims-before-"+str(key),channel=self.query["channel"],online_spec_code="M1",
                allocated_amount_cents=amount,cost_amount_cents=0,
                ship_time="2026-07-31 10:00:00",line_ship_time="2026-07-31 10:00:00").save()
        with versioned_netshop_facts():
            NetshopRow.objects.filter(source="jd_promotion").update(net_transaction_amount_cents=0,
                metrics_json={"spendCents":3000,"netTransactionAmountCents":0,"clicks":300,"impressions":3000,"netOrders":30})
            NetshopRow.objects.create(source_row_key="claims-ad-previous",source_row_hash=digest("claims-ad-previous"),
                first_import_batch_id="fixture",last_import_batch_id="fixture",source_row_number=100,
                source="jd_promotion",dataset="ad",platform="京东",shop_name=self.query["shop"],business_date="2026-07-31",
                sku_id="S0",spu_id="P1",spend_cents=100,net_transaction_amount_cents=1000,clicks=10,impressions=100,net_orders=1,
                metrics_json={"spendCents":100,"netTransactionAmountCents":1000,"clicks":10,"impressions":100,"netOrders":1},raw_json={})
        body = deepcopy(self.evidence_body)
        body.update(clientRequestId="claims-evidence",sources=deepcopy(self.sources),
            analysisRequest={"schemaVersion":"business-analysis-request-v1","question":"合成退款与推广候选引用",
                "requestedDimensions":["shop","sku","spu"],"requestedWindows":["current","previous"]})
        for key in ("sales","ads"):
            source = next(s for s in body["sources"] if s["key"] == key)
            body["sources"].append({**deepcopy(source),"key":key+"-previous","query":{**source["query"],"window":"previous"}})
        self.parent = self.collect_body(body)
        self.plan = mapping_plan.build(body["sources"],[{"salesKey":key,"masterKey":"master"} for key in ("sales","sales-previous")])
        self.report,_ = self.seed()
        self.run_id = self.publish(self.report)
        self.prepared = packages.prepare(self.run_id,self.admin)

    def publish(self, report):
        return store.publish(screening.prepare_for_report(report.id,self.admin),self.admin)["reference"]["id"]

    def handle(self, role="commerce"):
        return service.prepare(self.prepared,role,self.admin)

    def candidate(self, verified, rule="erp_refund_up_sales_not_up", dimension="shop"):
        values = json.loads(verified._index_json)["candidates"].values()
        return next(item["candidate"] for item in values if item["candidate"]["ruleId"] == rule
            and item["candidate"]["reference"]["dimension"] == dimension)

    def request(self, verified, **changes):
        return {"candidateId":self.candidate(verified)["candidateId"],"metric":"refundCents","field":"value",**changes}

    def test_exact_current_baseline_difference_and_fixed_native_mapped_references(self):
        verified = self.handle()
        for field,expected in (("value",200),("baseline",50),("difference",150)):
            value = service.resolve(verified,self.request(verified,field=field),self.admin)
            self.assertEqual(value["value"],expected)
            self.assertIs(type(value["value"]),int)
            self.assertEqual(value["reference"]["screening"]["id"],self.run_id)
            self.assertEqual(value["reference"]["source"]["key"],"sales")
            self.assertEqual(value["reference"]["baselineSource"]["key"],"sales-previous")
            self.assertTrue(value["verification"]["humanReviewRequired"])
            self.assertFalse(value["verification"]["agentReadVerified"])
            self.assertFalse(value["verification"]["causalityVerified"])
            self.assertFalse(value["dateCoverage"]["entityDailyCoverageVerified"])
            self.assertEqual(value["responseDigest"],digest({k:v for k,v in value.items() if k!="responseDigest"}))
        item = self.candidate(verified,"erp_refund_present","sku")
        mapped = service.resolve(verified,{"candidateId":item["candidateId"],"metric":"refundCents","field":"value"},self.admin)
        self.assertIn("pairKey",mapped["reference"]["row"])
        self.assertEqual(mapped["reference"]["masterSource"]["key"],"master")

    def test_prepare_and_repeated_resolve_never_read_facts_write_or_call_models(self):
        with patch.object(screening.Reader,"pages",side_effect=AssertionError("claims cannot rescan facts")), patch(
                "ai_assistant.provider.turn") as model, patch("ai_assistant.transport.execute_tool") as remote, CaptureQueriesContext(connection) as queries:
            verified = self.handle()
            with patch.object(packages,"prepare",side_effect=AssertionError("resolve must not rebuild five roles")), patch.object(
                    store,"_all",side_effect=AssertionError("resolve must not reread persisted result pages")):
                first = service.resolve(verified,self.request(verified),self.admin)
                self.assertEqual(first,service.resolve(verified,self.request(verified),self.admin))
        model.assert_not_called(); remote.assert_not_called()
        sql=[query["sql"].strip().lower() for query in queries]
        self.assertFalse(any("netshop_rows" in q or "sales_order_lines" in q for q in sql))
        self.assertFalse(any(q.startswith(("insert ","update ","delete ")) for q in sql))

    def test_exact_request_rejects_extra_json_value_aliases_unknown_metric_and_missing_baseline(self):
        verified = self.handle(); request = self.request(verified)
        invalid = [None,"{}",{**request,"value":200},{**request,"amount":200},{**request,"metadata":{}},
            {**request,"metric":"notARealMetric"},{**request,"metric":True},{**request,"candidateId":"0"*64},
            {**request,"field":"current"},{**request,"field":"baseline.value"},{**request,"field":False},
            {"candidateId":request["candidateId"],"metric":"refundCents"}]
        for item in invalid:
            with self.subTest(item=item),self.assertRaises(AiError):service.resolve(verified,item,self.admin)
        single = self.candidate(verified,"erp_refund_present")
        for field in ("baseline","difference"):
            with self.assertRaises(AiError):service.resolve(verified,{**request,"candidateId":single["candidateId"],"field":field},self.admin)
        with patch.object(service,"MAX_RESPONSE_BYTES",100),self.assertRaises(AiError):service.resolve(verified,request,self.admin)

    def test_role_owner_scope_and_current_account_revocation_are_rechecked(self):
        commerce = self.handle(); promotion = self.handle("promotion")
        with self.assertRaises(AiError):service.resolve(promotion,self.request(commerce),self.admin)
        empty = self.handle("market_b2b")
        with self.assertRaises(AiError):service.resolve(empty,self.request(commerce),self.admin)
        from sales.auth import Principal
        for actor in (self.viewer,self.user("claims-other@example.invalid","admin",None),Principal(self.admin.email,"scoped","admin",{"shops":["other"]})):
            with self.assertRaises(AiError):service.prepare(self.prepared,"commerce",actor)
            with self.assertRaises(AiError):service.resolve(commerce,self.request(commerce),actor)
        for role in (True,None,"admin","Commerce",["commerce"]):
            with self.assertRaises(AiError):service.prepare(self.prepared,role,self.admin)
        from access_control.models import AppUser
        AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        with self.assertRaises(AiError):service.resolve(commerce,self.request(commerce),self.admin)

    def test_late_actual_revocation_after_decode_discards_prepare_and_resolution(self):
        from access_control.models import AppUser
        verified = self.handle(); request = self.request(verified)
        old_status = AppUser.objects.get(email=self.admin.email).status
        original = service._index
        def revoked(decoded):
            value = original(decoded)
            AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            return value
        for invoke in (lambda:self.handle(),lambda:service.resolve(verified,request,self.admin)):
            AppUser.objects.filter(email=self.admin.email).update(status=old_status)
            with patch.object(service,"_index",revoked),self.assertRaises(AiError) as error:invoke()
            self.assertEqual(error.exception.status,403)

    def test_no_json_restore_and_returned_copies_or_entire_index_mutations_fail_closed(self):
        verified = self.handle(); request = self.request(verified)
        expected = service.resolve(verified,request,self.admin)
        response = service.resolve(verified,request,self.admin)
        response["entity"].clear(); response["reference"]["source"]["query"]["shop"]="changed"
        self.assertEqual(service.resolve(verified,request,self.admin),expected)
        for invalid in ({},json.loads(verified._index_json),SimpleNamespace(**{"_prepared":self.prepared}),"{}"):
            with self.assertRaises(AiError):service.resolve(invalid,request,self.admin)
        with self.assertRaises(AiError):service.VerifiedClaims(None,None,None,None,None,None)
        with self.assertRaises(FrozenInstanceError):verified._index_json="{}"
        index=json.loads(verified._index_json)
        index["candidates"][request["candidateId"]]["candidate"]["current"]["refundCents"]+=100
        object.__setattr__(verified,"_index_json",canonical(index))
        with self.assertRaises(AiError):service.resolve(verified,request,self.admin)

    def test_package_raw_and_self_rehashed_input_container_cannot_replace_actual_storage(self):
        verified = self.handle(); request = self.request(verified)
        role,original,fixed = self.prepared._packages[0]
        changed=json.loads(original._raw)
        changed["records"][0][1]["query"]["shop"]="伪造来源"
        object.__setattr__(original,"_raw",canonical(changed))
        with self.assertRaises(AiError):service.resolve(verified,request,self.admin)
        object.__setattr__(original,"_digest",digest(changed))
        with self.assertRaises(AiError):service.resolve(verified,request,self.admin)
        # Even a whole replacement tuple with a newly self-signed digest is
        # checked against a fresh build from actual ready storage in prepare.
        replacement=pure.Package(changed["header"],changed["directory"],changed["records"])
        object.__setattr__(self.prepared,"_packages",((role,replacement,replacement.package_digest),*self.prepared._packages[1:]))
        with self.assertRaises(AiError):self.handle()

    def test_other_screening_same_candidate_key_or_whole_container_cannot_be_substituted(self):
        first = self.handle(); request = self.request(first)
        report,_ = self.seed(); other_run = self.publish(report)
        other_prepared = packages.prepare(other_run,self.admin)
        other = service.prepare(other_prepared,"commerce",self.admin)
        self.assertNotEqual(first._package_digest,other._package_digest)
        # Reuse the old candidate ID and all its numbers inside another run's
        # private index: only the actual other package can define its IDs.
        object.__setattr__(other,"_index_json",first._index_json)
        with self.assertRaises(AiError):service.resolve(other,request,self.admin)
        object.__setattr__(first,"_prepared",other_prepared)
        with self.assertRaises(AiError):service.resolve(first,request,self.admin)

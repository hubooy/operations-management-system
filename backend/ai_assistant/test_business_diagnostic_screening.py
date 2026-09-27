"""Internal pagination unit tests and real sealed-report integration tests.

The pure pagination fixtures make no authority assertion. PostgreSQL tests use
real owning readers, persisted pages, immutable reports and full aggregators;
no provider call or fabricated scanner result stands in for their authority.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from . import business_diagnostic_screening as service, business_evidence, business_integrated
from . import test_business_integrated_guard as fixtures
from .policy import AiError, canonical, digest


class PaginationTests(unittest.TestCase):
    def test_complete_prefix_utf8_and_actual_next_offset(self):
        rows = [{"id":n, "value":"中文"*1500} for n in range(23)]
        offset, actual = 0, []
        while True:
            page = service._page({"schemaVersion":"unit-pagination"}, rows, offset)
            self.assertLessEqual(len(canonical(page).encode()), 38000)
            self.assertEqual(page["pageDigest"], digest({k:v for k,v in page.items() if k != "pageDigest"}))
            self.assertGreater(page["pagination"]["returned"], 0)
            self.assertLess(page["pagination"]["returned"], 20)
            actual.extend(page["items"])
            offset = page["pagination"]["nextOffset"]
            if offset is None: break
        self.assertEqual(actual, rows)

    def test_empty_end_oversize_and_strict_offsets(self):
        for offset in (True, False, 1.0, "0", -1, 3):
            with self.subTest(offset=offset), self.assertRaises(AiError): service._page({}, [1,2], offset)
        self.assertEqual(service._page({}, [1,2], 2)["items"], [])
        self.assertIsNone(service._page({}, [], 0)["pagination"]["nextOffset"])
        with self.assertRaises(AiError) as error: service._page({}, [{"wide":"中"*15000}], 0)
        self.assertEqual(error.exception.status, 413)

    def test_public_json_cannot_restore_verified_object(self):
        for value in ({}, "{}", SimpleNamespace(_binding_json="{}", _result_json="{}")):
            with self.assertRaises(AiError): service.coverage_page(value, None)
        with self.assertRaises(AiError): service.VerifiedScreening(None, {}, {})

    def test_internal_container_is_frozen_and_copies_summary(self):
        result = {"schemaVersion":"unit", "authority":{"safe":True}, "bindingDigest":"a", "planDigest":"b", "resultDigest":"c"}
        value = service.VerifiedScreening(service._TOKEN, {"reportId":"unit"}, result)
        result["authority"]["safe"] = False
        value.summary["authority"]["safe"] = False
        self.assertTrue(value.summary["authority"]["safe"])
        with self.assertRaises(AttributeError): value._result_json = "{}"

    def test_pagination_revalidates_before_and_after_and_discards_late_failure(self):
        result = {"schemaVersion":"unit", "authority":{}, "bindingDigest":"a", "planDigest":"b", "resultDigest":"c",
            "plan":{"families":[], "requestedCoverage":[]}, "prepared":{"coverage":{"tables":[]}, "partitions":[{
                "partitionKey":"partition", "matchedRows":1, "retainedRows":1, "omittedRows":0, "candidates":[{"id":1}]}]}}
        value = service.VerifiedScreening(service._TOKEN, {"reportId":"unit"}, result)
        for invoke in (lambda:service.coverage_page(value,None), lambda:service.candidate_page(value,None,"partition")):
            with patch.object(service,"_revalidate",side_effect=[None,AiError("late revoked")]) as verify, self.assertRaises(AiError): invoke()
            self.assertEqual(verify.call_count,2)


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class DiagnosticScreeningTests(djtest.TransactionTestCase):
    user = fixtures.BusinessIntegratedGuardTests.user
    call = fixtures.BusinessIntegratedGuardTests.call
    collect_body = fixtures.BusinessIntegratedGuardTests.collect_body
    bundle = fixtures.BusinessIntegratedGuardTests.bundle
    input_for = fixtures.BusinessIntegratedGuardTests.input_for
    insert = fixtures.BusinessIntegratedGuardTests.insert
    seed = fixtures.BusinessIntegratedGuardTests.seed

    def setUp(self):
        fixtures.BusinessIntegratedGuardTests.setUp(self)
        self.legacy_parent = self.parent
        body = deepcopy(self.evidence_body)
        body["clientRequestId"] = "screen-fixed-scope"
        body["sources"] = deepcopy(self.sources)
        body["analysisRequest"] = {"schemaVersion":"business-analysis-request-v1", "question":"固定经营范围筛查",
            "requestedDimensions":["shop","sku","spu"], "requestedWindows":["current"]}
        self.parent = self.collect_body(body)
        self.report, _ = self.seed()

    def coverage(self, verified):
        result, offset = [], 0
        while True:
            page = service.coverage_page(verified, self.admin, offset=offset)
            self.assertLessEqual(len(canonical(page).encode()),38000)
            result.extend(page["items"])
            offset = page["pagination"]["nextOffset"]
            if offset is None: return result

    def test_real_native_and_mapped_full_scan_no_business_queries_writes_or_models(self):
        calls, completed = [], []
        original = service.Reader.pages
        def pages(reader, key, *args, **kwargs):
            calls.append(key)
            yield from original(reader,key,*args,**kwargs)
            completed.append(key)
        preview = service.describe_for_report(self.report.id,self.admin)
        self.assertTrue(preview["plan"]["canScreen"])
        expected = []
        for descriptor in preview["plan"]["descriptors"]:
            expected.append(descriptor["source"]["key"])
            if descriptor["baseline"]: expected.append(descriptor["baseline"]["key"])
            if descriptor["mapping"]:
                expected.extend([descriptor["mapping"]["master"]["key"]]*(2 if descriptor["baseline"] else 1))
        with patch.object(service.Reader,"pages",pages), patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote, CaptureQueriesContext(connection) as queries:
            verified = service.prepare_for_report(self.report.id,self.admin)
            covered = self.coverage(verified)
        self.assertCountEqual(calls,expected)
        self.assertCountEqual(completed,expected)
        self.assertTrue(verified.summary["authority"]["executedTablesComplete"])
        self.assertEqual(len([item for item in covered if item["kind"]=="table"]),len(preview["plan"]["descriptors"]))
        self.assertEqual(len([item for item in covered if item["kind"]=="requested"]),len(preview["plan"]["requestedCoverage"]))
        for item in covered:
            if item["kind"] == "partition":
                page = service.candidate_page(verified,self.admin,item["value"]["partitionKey"])
                self.assertEqual(page["pagination"]["total"],item["value"]["retainedRows"])
        self.assertFalse(json.loads(verified._result_json)["prepared"]["authorityVerified"])
        model.assert_not_called(); remote.assert_not_called()
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT","UPDATE","DELETE")) for q in queries))
        self.assertFalse(any("netshop_rows" in q["sql"].lower() or "sales_order_lines" in q["sql"].lower() for q in queries))

    def test_missing_fixed_request_refuses_before_fact_scans(self):
        self.parent = self.legacy_parent
        report,_ = self.seed()
        with patch.object(service.Reader,"pages") as pages:
            preview = service.describe_for_report(report.id,self.admin)
            self.assertFalse(preview["plan"]["canScreen"])
            self.assertEqual(preview["plan"]["reason"],"missing_fixed_analysis_request")
            with self.assertRaises(AiError): service.prepare_for_report(report.id,self.admin)
            pages.assert_not_called()

    def test_plan_capacity_refuses_all_facts_instead_of_truncation(self):
        from business_analysis import screening_plan
        build = screening_plan.build
        def restricted(*args,**kwargs): return build(*args,**kwargs,limits={"maxPartitions":1})
        with patch.object(screening_plan,"build",restricted), patch.object(service.Reader,"pages") as pages:
            plan = service.describe_for_report(self.report.id,self.admin)["plan"]
            self.assertFalse(plan["canScreen"])
            self.assertGreater(len(plan["descriptors"]),1)
            with self.assertRaises(AiError): service.prepare_for_report(self.report.id,self.admin)
            pages.assert_not_called()

    def test_last_fact_failure_and_context_exit_revocation_publish_nothing(self):
        original = service.Reader.pages
        def corrupt(reader,key,*args,**kwargs):
            yield from original(reader,key,*args,**kwargs)
            raise AiError("synthetic corrupt tail")
        with patch.object(service.Reader,"pages",corrupt), self.assertRaises(AiError):
            service.prepare_for_report(self.report.id,self.admin)
        from access_control.models import AppUser
        stream = service.stream_table
        @contextmanager
        def revoke(*args,**kwargs):
            with stream(*args,**kwargs) as opened: yield opened
            AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        with patch.object(service,"stream_table",revoke), self.assertRaises(AiError):
            service.prepare_for_report(self.report.id,self.admin)

    def test_cross_owner_scope_and_page_time_revocation(self):
        verified = service.prepare_for_report(self.report.id,self.admin)
        other = self.user("screen-other@example.invalid","admin",None)
        for actor in (other,self.viewer):
            with self.assertRaises(AiError): service.describe_for_report(self.report.id,actor)
            with self.assertRaises(AiError): service.coverage_page(verified,actor)
        from sales.auth import Principal
        scoped = Principal(self.admin.email,"scope","admin",{"shops":["other"]})
        with self.assertRaises(AiError): service.coverage_page(verified,scoped)
        from access_control.models import AppUser
        AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        with self.assertRaises(AiError): service.coverage_page(verified,self.admin)

    def test_budget_binding_is_verified_without_resolving_budget_results(self):
        report,_ = self.seed(budget=True)
        with patch("ai_assistant.business_budget_store.load",side_effect=AssertionError("budget math must not run")):
            verified = service.prepare_for_report(report.id,self.admin)
        self.assertIsNotNone(json.loads(verified._binding_json)["budgetRef"])

    def test_live_report_reloaded_and_input_tamper_rejected_not_trusted_from_object(self):
        verified = service.prepare_for_report(self.report.id,self.admin)
        bound = business_integrated.bound
        def tamper(*args,**kwargs):
            actual,snapshot,reference,evidence,sources = bound(*args,**kwargs)
            actual.workflow.input_json = canonical({**reference,"question":"changed after bound"})
            return actual,snapshot,reference,evidence,sources
        with patch.object(business_integrated,"bound",tamper), self.assertRaises(AiError):
            service.coverage_page(verified,self.admin)

    def test_real_empty_and_missing_dates_are_not_complete_source_date_coverage(self):
        # Retain the real fixed sales/master pair required by this report profile;
        # a shop-only request executes ERP and promotion, without mapped tables.
        for label, first, last, expected_status in (
                ("partial","2026-08-01","2026-08-02","missing_dates"),
                ("empty","2026-08-02","2026-08-02","no_records")):
            with self.subTest(label=label):
                body = deepcopy(self.evidence_body)
                body["clientRequestId"] = "screen-date-"+label
                body["sources"] = deepcopy(self.sources)
                for source in body["sources"]:
                    source["query"].update(startDate=first,endDate=last)
                body["analysisRequest"] = {"schemaVersion":"business-analysis-request-v1", "question":"核验实际来源日期覆盖",
                    "requestedDimensions":["shop"], "requestedWindows":["current"]}
                self.parent = self.collect_body(body)
                from business_analysis import mapping_plan
                self.plan = mapping_plan.build(body["sources"],[{"salesKey":"sales","masterKey":"master"}])
                report,_ = self.seed()
                verified = service.prepare_for_report(report.id,self.admin)
                authority = verified.summary["authority"]
                self.assertTrue(authority["requestedCoveragePlanned"])
                self.assertTrue(authority["requestedTablesExecutedComplete"])
                self.assertFalse(authority["requestedSourceDateCoverageComplete"])
                self.assertFalse(authority["entityDailyCoverageVerified"])
                self.assertNotIn("requestedCoverageComplete",authority)
                tables = [item["value"] for item in self.coverage(verified) if item["kind"]=="table"]
                self.assertTrue(tables)
                self.assertEqual({table["sourceCoverage"]["status"] for table in tables},{expected_status})

"""Signed reader routes over real sealed, mapped and published fixtures.

These isolated PostgreSQL tests do not dispatch models or enable a workflow.
Fixture classes stay behind their module alias to avoid duplicate discovery.
"""
from dataclasses import replace
import json
from urllib.parse import urlencode
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from sales.tests.factories import signed_headers, TEST_SECRET
from . import test_business_screening_tools as fixtures
from .policy import digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class ScreeningRouteTests(djtest.TransactionTestCase):
    user = fixtures.ScreeningToolsTests.user
    call = fixtures.ScreeningToolsTests.call
    collect_body = fixtures.ScreeningToolsTests.collect_body
    bundle = fixtures.ScreeningToolsTests.bundle
    input_for = fixtures.ScreeningToolsTests.input_for
    insert = fixtures.ScreeningToolsTests.insert
    seed = fixtures.ScreeningToolsTests.seed
    setUp = fixtures.ScreeningToolsTests.setUp
    screening_bundle = fixtures.ScreeningToolsTests.screening_bundle
    insert_screening = fixtures.ScreeningToolsTests.insert_screening
    seed_ready = fixtures.ScreeningToolsTests.seed_ready

    def route(self, report, operation, *, params=None, actor=None, role="ai_reader",
              method="GET", signed=True, suffix="", report_id=None):
        actor = actor or self.admin
        snapshot = json.loads(report.snapshot_json)
        query = {"runId": self.parent.id,
                 "screeningId": snapshot.get("screeningIntent", {}).get("id", "screening-missing"),
                 **(params or {})}
        url = f"/api/ai/reports/{report_id or report.id}/screening/{operation}?" + urlencode(query) + suffix
        body = b"{}" if method == "POST" else b""
        headers = signed_headers(url, email=actor.email, role=actor.role, scope=actor.scope,
                                 method=method, body=body) if signed else {}
        with patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}), djtest.override_settings(
                DJANGO_INTERNAL_SECRET=TEST_SECRET, DJANGO_PROCESS_ROLE=role), patch("ai_assistant.views.authority"):
            return self.client.generic(method, url, data=body, content_type="application/json", headers=headers)

    def checked(self, response, schema):
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertLessEqual(len(response.content), 38000)
        value = response.json()
        self.assertEqual(value["schemaVersion"], schema)
        self.assertEqual(value["pageDigest"], digest({k: v for k, v in value.items() if k != "pageDigest"}))
        return value

    def test_signed_ready_package_native_mapped_and_budget_reads_have_no_writes_or_dispatch(self):
        report = self.seed_ready(mapped=True, budget=True)
        snapshot = json.loads(report.snapshot_json)
        reference = json.loads(report.workflow.input_json)
        with patch("ai_assistant.provider.turn") as model, patch("ai_assistant.transport.execute_tool") as remote, \
                CaptureQueriesContext(connection) as queries:
            package = self.checked(self.route(report, "package", params={"role": "commerce"}),
                                   "business-screening-role-package-v1")
            self.assertEqual(package["reportId"], report.id)
            self.assertEqual(package["evidenceRunId"], self.parent.id)
            self.assertEqual(package["role"], "commerce")
            self.assertFalse(package["authorityVerified"])
            self.assertGreater(package["totalRecords"], 0)
            native = self.checked(self.route(report, "analysis", params={
                "mode": "native", "dimension": "sku", "sourceKey": "ads", "offset": "0"}),
                "business-screening-analysis-v1")
            mapped = self.checked(self.route(report, "analysis", params={
                "mode": "mapped", "dimension": "sku", "pairKey": snapshot["mappingPlan"]["pairs"][0]["pairKey"]}),
                "business-screening-analysis-v1")
            budget = self.checked(self.route(report, "budget"), "business-screening-budget-v1")
            for response in (native, mapped, budget):
                self.assertEqual(response["reference"], reference)
            self.assertEqual(sum(row["metrics"]["spendCents"]["value"] for row in native["table"]["rows"]), 6000)
            self.assertEqual(sum(row["metrics"]["netSalesCents"]["value"] for row in mapped["table"]["rows"]), 120000)
            self.assertEqual(budget["budget"]["pagination"]["total"], 2)
            self.assertIsNone(budget["budget"]["pagination"]["nextOffset"])
        model.assert_not_called()
        remote.assert_not_called()
        sql = [entry["sql"].lstrip().upper() for entry in queries]
        self.assertFalse(any(statement.startswith(("INSERT", "UPDATE", "DELETE")) for statement in sql))
        self.assertFalse(any("NETSHOP_ROWS" in statement or "SALES_ORDER_LINES" in statement for statement in sql))

    def test_signature_method_process_and_query_contract_reject_before_business_reads(self):
        report = self.seed_ready(mapped=True, budget=True)
        selectors = {"package": {"role": "commerce"}, "analysis": {
            "mode": "native", "dimension": "sku", "sourceKey": "ads"}, "budget": {}}
        with patch("ai_assistant.provider.turn") as model, patch("ai_assistant.transport.execute_tool") as remote, \
                CaptureQueriesContext(connection) as queries:
            for operation, params in selectors.items():
                with self.subTest(operation=operation):
                    self.assertEqual(self.route(report, operation, params=params, signed=False).status_code, 401)
                    self.assertEqual(self.route(report, operation, params=params, role="ai_writer").status_code, 403)
                    self.assertEqual(self.route(report, operation, params=params, method="POST").status_code, 405)
                    for suffix in ("&unknown=1", "&runId=another", "&offset=0&offset=1", "&offset=01", "&offset=true"):
                        response = self.route(report, operation, params=params, suffix=suffix)
                        self.assertEqual(response.status_code, 400, response.content)
            for params in ({"mode": "native", "dimension": "sku", "sourceKey": "ads", "pairKey": "a"*64},
                           {"mode": "mapped", "dimension": "sku", "pairKey": "a"*64, "sourceKey": "ads"}):
                response = self.route(report, "analysis", params=params)
                self.assertEqual(response.status_code, 400, response.content)
        model.assert_not_called()
        remote.assert_not_called()
        self.assertFalse(any(entry["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for entry in queries))

    def test_owner_scope_fixed_identity_ready_and_old_profile_fail_closed(self):
        from access_control.models import AppUser

        report = self.seed_ready(mapped=True, budget=True)
        unready = self.seed_ready(publish=False)
        other = self.user("screening-route-other@example.invalid", "admin", None)
        for actor in (other, self.viewer):
            response = self.route(report, "package", params={"role": "commerce"}, actor=actor)
            self.assertEqual(response.status_code, 404, response.content)
        for key in ("runId", "screeningId"):
            response = self.route(report, "package", params={"role": "commerce", key: "another"})
            self.assertEqual(response.status_code, 403, response.content)
        response = self.route(report, "package", params={"role": "commerce"}, report_id="missing-report")
        self.assertEqual(response.status_code, 404, response.content)
        response = self.route(unready, "package", params={"role": "commerce"})
        self.assertEqual(response.status_code, 409, response.content)
        for operation, params in (("package", {"role": "commerce"}), ("analysis", {
                "mode": "native", "dimension": "sku", "sourceKey": "ads"}), ("budget", {})):
            response = self.route(self.report, operation, params=params)
            self.assertEqual(response.status_code, 409, response.content)
        scope = {"warehouses": [], "channels": [], "platforms": ["京东"]}
        AppUser.objects.filter(email=self.admin.email).update(scope=scope)
        for actor, status in ((self.admin, 403), (replace(self.admin, scope=scope), 404)):
            for operation, params in (("package", {"role": "commerce"}), ("analysis", {
                    "mode": "native", "dimension": "sku", "sourceKey": "ads"}), ("budget", {})):
                response = self.route(report, operation, params=params, actor=actor)
                self.assertEqual(response.status_code, status, response.content)

    def test_old_integrated_http_directory_analysis_budget_are_json_readonly_responses(self):
        # The inherited actual report has no budget. Reuse it for directory and
        # native analysis, and prepare one real old-profile fixed budget report.
        budget_report,_ = self.seed(budget=True)
        cases = ((self.report,'integrated-directory',{},'business-integrated-directory-v1'),
            (self.report,'integrated-analysis-table',{'mode':'native','dimension':'sku','sourceKey':'ads'},'business-integrated-analysis-v1'),
            (budget_report,'integrated-budget',{},'business-integrated-budget-v1'))
        with (patch('ai_assistant.provider.turn') as model, patch('ai_assistant.transport.execute_tool') as remote,
                CaptureQueriesContext(connection) as queries):
            for report,operation,params,schema in cases:
                with self.subTest(operation=operation):
                    url=f'/api/ai/reports/{report.id}/{operation}?'+urlencode({'runId':self.parent.id,**params})
                    headers=signed_headers(url,email=self.admin.email,role=self.admin.role,scope=self.admin.scope)
                    with (patch.dict('os.environ',{'TERUISI_DJANGO_INTERNAL_SECRET':TEST_SECRET}),
                            djtest.override_settings(DJANGO_INTERNAL_SECRET=TEST_SECRET,DJANGO_PROCESS_ROLE='ai_reader'),
                            patch('ai_assistant.views.authority')):
                        result=self.client.get(url,headers=headers)
                    value=self.checked(result,schema)
                    self.assertEqual(value['reference'],json.loads(report.workflow.input_json))
        model.assert_not_called();remote.assert_not_called()
        sql=[entry['sql'].lstrip().upper() for entry in queries]
        self.assertFalse(any(statement.startswith(('INSERT','UPDATE','DELETE')) for statement in sql))
        self.assertFalse(any('NETSHOP_ROWS' in statement or 'SALES_ORDER_LINES' in statement for statement in sql))

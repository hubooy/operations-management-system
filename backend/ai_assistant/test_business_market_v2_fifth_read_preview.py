"""Actual paused-root fifth-tool preview; no Agent or provider receipt."""
from copy import deepcopy
from unittest.mock import patch

from django import test as djtest
from django.db import connection

from access_control.models import AppUser
from . import business_market_v2_fifth_read_contract as contract
from . import business_market_v2_fifth_read_preview as service
from . import business_market_v2_material_admission as material_owner
from . import business_market_v2_admitted_paused as admitted
from . import business_promotion_market_admission as admission
from . import business_promotion_market_runtime_v2_contract as runtime
from . import business_market_v2_parked_creation as parked
from . import models as m
from . import test_business_market_v2_material_admission as fixture
from .policy import AiError, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketV2FifthReadPreviewTests(djtest.TransactionTestCase):
    user = fixture.MarketV2MaterialAdmissionTests.user
    request_body = fixture.MarketV2MaterialAdmissionTests.request_body
    current_catalog = fixture.MarketV2MaterialAdmissionTests.current_catalog
    create_fixed_report = fixture.MarketV2MaterialAdmissionTests.create_fixed_report
    planned_evidence_body = fixture.MarketV2MaterialAdmissionTests.planned_evidence_body
    setUp = fixture.MarketV2MaterialAdmissionTests.setUp
    selector = fixture.MarketV2MaterialAdmissionTests.selector
    parked_id = fixture.MarketV2MaterialAdmissionTests.parked_id
    _attest_as_role = staticmethod(fixture.MarketV2MaterialAdmissionTests._attest_as_role)

    def read_as_reader(self, *args, **kwargs):
        # The 0056 projection is intentionally callable only by the genuine
        # login reader role. The isolated test owner can impersonate it here.
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION AUTHORIZATION teruisi_ai_reader")
        try:
            return service.read(*args, **kwargs)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("RESET SESSION AUTHORIZATION")

    def roots(self):
        parked_id = self.parked_id()
        material = material_owner.prepare_candidate(parked_id, self.admin)
        self._attest_as_role(parked_id, material)
        created = admitted.create({"schemaVersion": admitted.REQUEST_SCHEMA,
            "clientRequestId": "fifth-preview-target",
            "parkedReportId": parked_id}, self.admin)
        selected = self.selector()
        fixed = admission.require_observed(self.report.id, selected, self.admin)
        candidate = runtime.prepare(fixed, with_budget=False)
        call = {"schemaVersion": contract.SCHEMA,
            "admittedReportId": created["reportId"],
            "jobId": "future-market-job", "providerDispatchId": "future-market-provider",
            "providerCallId": "future-market-call", "role": "market_b2b",
            "marketManifestDigest": material["candidate"]["manifestDigest"],
            "marketContextDigest": candidate["marketContextDigest"]}
        return parked_id, created, selected, call

    def args(self, call, mode, **rest):
        return {"reportId": call["admittedReportId"],
            "marketContextDigest": call["marketContextDigest"],
            "mode": mode, **rest}

    def test_summary_is_bound_to_admitted_material_but_not_agent_read(self):
        parked_id, created, selected, call = self.roots()
        before = (m.AiAgentJobs.objects.count(),
            m.AiAgentProviderDispatches.objects.count(),
            m.AiAgentToolDispatches.objects.count(),
            m.AiAgentToolResults.objects.count())
        with patch("ai_assistant.transport.execute_tool") as model_tool:
            result = self.read_as_reader(call, selected, self.args(call, "summary"),
                self.admin)
        model_tool.assert_not_called()
        self.assertEqual(result["admittedReportId"], created["reportId"])
        self.assertEqual(result["sourceReportId"], self.report.id)
        self.assertEqual(result["marketManifestDigest"], call["marketManifestDigest"])
        self.assertEqual([table["view"] for table in result["payload"]["tables"]],
            ["price_band_summary", "price_band_members", "rank_entry_exit"])
        self.assertTrue(result["serverFullMarketMaterialVerified"])
        for key in ("sameJobProviderPersisted", "persistedRead", "registeredTool",
                "authorityVerified"):
            self.assertFalse(result[key])
        self.assertEqual(result["resultDigest"], digest({key: value for key, value
            in result.items() if key != "resultDigest"}))
        self.assertEqual((m.AiAgentJobs.objects.count(),
            m.AiAgentProviderDispatches.objects.count(),
            m.AiAgentToolDispatches.objects.count(),
            m.AiAgentToolResults.objects.count()), before)
        self.assertEqual(m.AiReportRun.objects.get(pk=parked_id).workflow.status,
            "paused")

    def test_page_and_row_have_server_selected_citation_and_numeric_cell(self):
        _, _, selected, call = self.roots()
        for view in ("price_band", "rank_entry_exit"):
            page = self.read_as_reader(call, selected, self.args(call, "page", view=view,
                offset=0, limit=20), self.admin)
            self.assertTrue(page["citationBases"])
            row = page["payload"]["rows"][0]
            numeric = ({"metric": "sampleGmvLowerCents", "field": "presentRows"}
                if view == "price_band" else None)
            exact = self.read_as_reader(call, selected, self.args(call, "row", view=view,
                rowIndex=row["rowIndex"], rowId=row["rowId"]), self.admin,
                numeric_selection=numeric)
            self.assertEqual(exact["citationBases"][0]["rowId"], row["rowId"])
            self.assertEqual(exact["payload"]["row"], row)
            self.assertFalse(exact["persistedRead"])
            if numeric is not None:
                candidate = exact["verifiedNumericCandidate"]
                self.assertEqual(candidate["reference"]["reportId"],
                    call["admittedReportId"])
                self.assertEqual(candidate["reference"]["jobId"], call["jobId"])
                self.assertEqual(candidate["number"]["population"],
                    "market_top_sample_only")
                self.assertFalse(candidate["sameJobProviderPersisted"])

    def test_wrong_owner_role_selector_material_and_late_revocation_reject(self):
        _, _, selected, call = self.roots()
        wrong = self.user("fifth-market-other@example.invalid", "admin", None)
        with self.assertRaises(AiError):
            self.read_as_reader(call, selected, self.args(call, "summary"), wrong)
        role = {**call, "role": "commerce"}
        with self.assertRaises(AiError):
            self.read_as_reader(role, selected, self.args(role, "summary"), self.admin)
        other = deepcopy(selected)
        other["bands"][0]["key"] += "_other"
        with self.assertRaises(AiError):
            self.read_as_reader(call, other, self.args(call, "summary"), self.admin)
        manifest = {**call, "marketManifestDigest": "0"*64}
        with self.assertRaises(AiError):
            self.read_as_reader(manifest, selected, self.args(manifest, "summary"),
                self.admin)
        bad_report = {**call, "admittedReportId": "other-report"}
        with self.assertRaises(AiError):
            self.read_as_reader(bad_report, selected,
                self.args(bad_report, "summary"), self.admin)
        def revoke(event):
            if event == {"stage": "market_composite_export", "phase": "complete"}:
                with connection.cursor() as cursor:
                    cursor.execute("RESET SESSION AUTHORIZATION")
                try:
                    AppUser.objects.filter(email=self.admin.email).update(status="disabled")
                finally:
                    with connection.cursor() as cursor:
                        cursor.execute("SET SESSION AUTHORIZATION teruisi_ai_reader")
        with self.assertRaises(AiError):
            self.read_as_reader(call, selected, self.args(call, "summary"), self.admin,
                checkpoint=revoke)

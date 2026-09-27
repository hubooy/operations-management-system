"""Candidate-only same-job market numbers from signed sealed-reader GETs."""
from copy import deepcopy
import hashlib
from unittest.mock import patch
from urllib.parse import urlencode

from django import test as djtest

from sales.tests.factories import TEST_SECRET, signed_headers
from . import business_market_numeric_claims as service
from . import models as m
from . import test_business_market_observation as fixtures
from . import test_business_market_dynamics as market_fixture
from .policy import AiError, canonical, digest, mutation, uid


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketNumericCandidateTests(djtest.TransactionTestCase):
    user = fixtures.BusinessMarketObservationTests.user
    call = fixtures.BusinessMarketObservationTests.call
    bundle = fixtures.BusinessMarketObservationTests.bundle
    input_for = fixtures.BusinessMarketObservationTests.input_for
    insert = fixtures.BusinessMarketObservationTests.insert
    seed = fixtures.BusinessMarketObservationTests.seed
    collect_body = fixtures.BusinessMarketObservationTests.collect_body
    market_row = fixtures.BusinessMarketObservationTests.market_row
    setUp = fixtures.BusinessMarketObservationTests.setUp

    def job(self, role="market_b2b"):
        with mutation(self.admin):
            return m.AiAgentJobs.objects.create(id=uid("market-candidate-job"),
                owner_email=self.admin.email, scope_json="null",
                client_request_id=uid("market-candidate-client"),
                request_digest="a"*64, task="未注册市场阅读候选",
                workflow_run_id=self.report.workflow_id,
                workflow_node_key=role, status="completed", phase="completed")

    def signed_get(self, suffix, params):
        url = f"/api/ai/reports/{self.report.id}/{suffix}?" + urlencode(params)
        with (patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}),
                djtest.override_settings(DJANGO_INTERNAL_SECRET=TEST_SECRET,
                    DJANGO_PROCESS_ROLE="ai_reader"),
                patch("ai_assistant.views.authority")):
            response = self.client.get(url,
                headers=signed_headers(url, email=self.admin.email))
        self.assertEqual(response.status_code, 200, response.content)
        return response.json(), hashlib.sha256(url.encode("utf-8")).hexdigest()

    def rank_claim(self, job):
        args = {"currentSourceKey": "market", "baselineSourceKey": "market-prior",
            "currentObservationDate": "2026-08-03",
            "baselineObservationDate": "2026-07-31", "offset": "0", "limit": "20"}
        page, _ = self.signed_get("market-observation", args)
        row = next(item for item in page["table"]["rows"]
            if item["skuId"] == "market-0")
        query = {**{key: value for key, value in args.items()
            if key not in {"offset", "limit"}},
            "rowIndex": str(row["rowIndex"]), "rowId": row["rowId"]}
        found, request_digest = self.signed_get("market-observation", query)
        ref = {"jobId": job.id, "role": job.workflow_node_key,
            "reportId": self.report.id, "sourceKey": "market",
            "view": "rank_entry_exit", "baselineKey": "market-prior",
            "currentObservationDate": "2026-08-03",
            "baselineObservationDate": "2026-07-31",
            "tableBindingDigest": found["binding"]["tableBindingDigest"],
            "rowIndex": row["rowIndex"], "rowId": row["rowId"],
            "metric": "rankImprovement", "field": "value",
            "attribution": "market_top_sample_only"}
        receipt = {**ref, "schemaVersion": "business-market-agent-read-candidate-v1",
            "ownerEmail": self.admin.email, "bindingDigest": found["bindingDigest"],
            "responseDigest": found["responseDigest"],
            "signedRequestDigest": request_digest}
        return ref, receipt, row

    def price_claim(self, job):
        args = {"sourceKey": "market", "view": "price_band",
            "bands": canonical(market_fixture.BANDS), "offset": "0", "limit": "20"}
        page, _ = self.signed_get("market-dynamics", args)
        row = next(item for item in page["table"]["rows"]
            if item["bandKey"] == "low")
        query = {**{key: value for key, value in args.items()
            if key not in {"offset", "limit"}},
            "rowIndex": str(row["rowIndex"]), "rowId": row["rowId"]}
        found, request_digest = self.signed_get("market-dynamics", query)
        ref = {"jobId": job.id, "role": job.workflow_node_key,
            "reportId": self.report.id, "sourceKey": "market",
            "view": "price_band", "bandsDigest": digest(market_fixture.BANDS),
            "bandKey": row["bandKey"],
            "tableBindingDigest": found["binding"]["tableBindingDigest"],
            "rowIndex": row["rowIndex"], "rowId": row["rowId"],
            "metric": "sampleGmvLowerCents", "field": "value",
            "attribution": "market_top_sample_only"}
        receipt = {**ref, "schemaVersion": "business-market-agent-read-candidate-v1",
            "ownerEmail": self.admin.email, "bindingDigest": found["bindingDigest"],
            "responseDigest": found["responseDigest"],
            "signedRequestDigest": request_digest}
        return ref, receipt, row

    def test_real_signed_rows_recompute_numbers_but_never_claim_agent_ledger(self):
        job = self.job()
        for ref, receipt, row, bands in ((*self.rank_claim(job), None),
                (*self.price_claim(job), market_fixture.BANDS)):
            with self.subTest(view=ref["view"]):
                prepared = service.prepare(job.id, ref, receipt, self.admin, bands=bands)
                actual = service.resolve(prepared, self.admin)
                self.assertEqual(actual["value"],
                    row["rankImprovement"] if ref["view"] == "rank_entry_exit"
                    else row["metrics"]["sampleGmvLowerCents"]["value"])
                self.assertFalse(actual["verification"]["agentReadPersisted"])
                self.assertFalse(actual["verification"]["authorityVerified"])
                self.assertFalse(actual["ownSalesAttributionVerified"])

    def test_wrong_role_job_report_row_and_forged_receipt_reject(self):
        job = self.job()
        ref, receipt, _ = self.rank_claim(job)
        for change in (lambda r,v: r.update(jobId="other"),
                lambda r,v: r.update(role="promotion"),
                lambda r,v: r.update(reportId=self.old_report.id),
                lambda r,v: r.update(metric="erpNetSalesCents"),
                lambda r,v: v.update(responseDigest="0"*64),
                lambda r,v: v.update(rowId="0"*64)):
            changed, claimed = deepcopy(ref), deepcopy(receipt)
            change(changed, claimed)
            with self.assertRaises(AiError):
                service.prepare(job.id, changed, claimed, self.admin)
        prepared = service.prepare(job.id, ref, receipt, self.admin)
        with self.assertRaises(AiError):
            service.resolve(prepared.value, self.admin)

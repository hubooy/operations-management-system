"""Isolated PostgreSQL checks for the internal, non-reporting mixed-v3 seal."""
import json
import importlib
import time
from unittest.mock import patch

from django.db import DatabaseError, connection, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from access_control.models import AccessRole, AppUser
from business_analysis import evidence_seal_v3 as seal_contract
from business_analysis import finance_collection_state as finance_verifier
from business_analysis.contracts import PageReconciler, canonical, comparison_periods
from business_analysis.test_finance_collection_state import owned_page, sources as finance_fixture
from sales.auth import Principal
from sales.tests.factories import signed_headers, TEST_SECRET

from . import business_daily_collection_v3 as daily, business_evidence_v3 as plan
from . import business_evidence as legacy, business_finance_collection_v3 as finance
from . import business_v3_catalog as catalog
from . import business_report_candidate_v3 as candidate
from . import business_v3_report_intent as intent, business_v3_seal as seal
from . import business_v3_source_read as source_read, models as m, transport
from .policy import AiError, digest, uid


def tool(name):
    return {"name": name, "risk": "read_only", "allowedRoles": ["admin"],
        "scopePolicy": "unscoped_only",
        "execution": {"mode": "direct", "allowedSurfaces": ["business_collection"]}}


class BusinessV3SealTests(TestCase):
    def setUp(self):
        if connection.vendor != "postgresql": self.skipTest("v3 seal requires PostgreSQL")
        self.principal = Principal("v3-seal@example.test", "Synthetic", "admin", None)
        role, _ = AccessRole.objects.get_or_create(code="admin", defaults={"rank": 40, "label": "Admin"})
        now = timezone.now()
        self.actor = AppUser.objects.create(email=self.principal.email, display_name="Synthetic",
            role=role, status="active", scope=None, version=1, created_at=now, updated_at=now)
        _, self.finance_query, publication, rows = finance_fixture()
        self.finance_page = owned_page(self.finance_query, publication, rows, offset=0, total=len(rows))
        self.daily_query = {"platform": "京东", "shop": "测试店", "channel": "京东",
            "startDate": "2026-08-20", "endDate": "2026-09-18", "window": "current"}
        records = [{"rowId": "1", "platform": "京东", "shopName": "测试店", "channel": "京东",
            "date": "2026-08-20", "metrics": {"salesCents": 100}}]
        self.daily_page = {"schemaVersion": "business-analysis-v1", "sourceRef": "b" * 64,
            "sourceRevision": "1:2", "source": "erp_sales", "sourceDataset": None,
            "monetaryUnit": "CNY_CENT", "filters": {**self.daily_query,
                "periods": comparison_periods(self.daily_query["startDate"], self.daily_query["endDate"]),
                "limit": 100}, "items": records,
            "control": {"rowCount": 1, "typedTotals": {"salesCents": 100}},
            "pageEvidence": {"rowCount": 1, "sha256": digest(records)},
            "pagination": {"limit": 100, "hasMore": False, "nextCursor": None},
            "metricSemantics": None}
        body = {"schemaVersion": "business-evidence-v3", "clientRequestId": uid("client"),
            "sources": [{"key": "sales", "domain": "sales", "query": self.daily_query},
                {"key": "finance", "domain": "finance", "query": self.finance_query}],
            "analysisRequest": {"schemaVersion": "business-analysis-request-v1",
                "question": "封存财报与日来源", "requestedDimensions": ["shop"],
                "requestedWindows": ["current"]}}
        self.run_id = plan.create(body, self.principal)["item"]["id"]

    def signed_page(self, name, arguments, principal, **kwargs):
        page = self.finance_page if name == finance.TOOL else self.daily_page
        m.AiToolAuditLogs.objects.create(id=uid("audit"), request_id=kwargs["request_id"],
            invocation_id=uid("invocation"), actor_email=self.principal.email, actor_role="admin",
            surface="business_collection", tool_name=name, arguments_json="{}", status="succeeded",
            duration_ms=1, response_digest=digest(canonical(page)))
        return {"ok": True, "toolName": name, "data": page}

    def collect(self):
        entries = [tool(finance.TOOL), tool(daily.INITIAL_TOOL)]
        with patch.object(transport, "catalog", return_value=entries), \
                patch.object(transport, "execute_tool", side_effect=self.signed_page):
            finance.advance_finance_source(self.run_id, "finance", 1, self.principal, "seal-finance")
            daily.advance_daily_source(self.run_id, "sales", 2, self.principal, "seal-sales")

    def test_complete_mixed_sources_seal_and_reopen_internal_read(self):
        self.collect()
        result = seal.finish(self.run_id, 3, self.principal)
        self.assertEqual(result["status"], "sealed")
        self.assertFalse(result["reportGenerationSupported"])
        self.assertFalse(result["seal"]["sourceAuthorityVerified"])
        sales = next(item for item in result["seal"]["sources"] if item["sourceKey"] == "sales")
        self.assertEqual(sales["coverage"]["missingRowDateCount"], 29)
        self.assertEqual(seal.verify(self.run_id, self.principal)["seal"], result["seal"])
        with self.assertRaises(AiError):
            daily.advance_daily_source(self.run_id, "sales", 4, self.principal, "after-seal")
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessEvidenceRun.objects.filter(pk=self.run_id).update(state_json="{}")
        raw = canonical(self.daily_page)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessEvidenceChunk.objects.create(id=uid("chunk"), run_id=self.run_id,
                source_key="sales", sequence=2, payload_json=raw, payload_digest=digest(raw))
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessSourceToolReceipt.objects.filter(run_id=self.run_id).first().delete()
        self.assertEqual(seal.verify(self.run_id, self.principal)["seal"], result["seal"])

    def test_unfinished_cas_and_revoked_actor_cannot_seal(self):
        with self.assertRaises(AiError): seal.finish(self.run_id, 1, self.principal)
        self.collect()
        with self.assertRaises(AiError): seal.finish(self.run_id, 2, self.principal)
        with transaction.atomic():
            self.actor.status = "inactive"; self.actor.save(update_fields=["status"])
            with self.assertRaises(AiError): seal.finish(self.run_id, 3, self.principal)
            transaction.set_rollback(True)
        self.assertEqual(m.AiBusinessEvidenceRun.objects.get(pk=self.run_id).status, "collecting")

    def test_direct_sql_sealed_status_with_forged_digest_is_not_authority(self):
        self.collect()
        row, built, sources, _ = catalog.load(self.run_id, self.principal)
        queries = {entry["key"]: entry["query"] for entry in built["entries"]}
        proofs = [seal._source_proof(row, source, queries[source["source_key"]], self.principal)
                  for source in sources]
        state = seal_contract.make(run_id=row.id, evidence_version=row.version+1,
            plan_digest=digest(row.plan_json), catalog_digest=built["header"]["catalogDigest"],
            sources=proofs, stored_bytes=row.stored_bytes)
        state["sealedDigest"] = "0" * 64
        with transaction.atomic():
            m.AiBusinessEvidenceRun.objects.filter(pk=row.id).update(status="sealed",
                version=row.version+1, state_json=canonical(state))
        self.assertEqual(m.AiBusinessEvidenceRun.objects.get(pk=row.id).status, "sealed")
        with self.assertRaises(AiError): seal.verify(self.run_id, self.principal)

    def test_old_direct_finance_fact_stays_unsealed_without_receipt(self):
        parent = m.AiBusinessEvidenceRun.objects.get(pk=self.run_id)
        source = m.AiBusinessEvidenceSource.objects.get(run=parent, source_key="finance")
        state = finance_verifier.consume(None, self.finance_page, trusted_query=self.finance_query)
        raw = canonical(self.finance_page)
        with transaction.atomic():
            m.AiBusinessEvidenceChunk.objects.create(id=uid("chunk"), run=parent,
                source_key=source.source_key, sequence=1, payload_json=raw, payload_digest=digest(raw))
            m.AiBusinessEvidenceSource.objects.filter(pk=source.pk).update(version=2,
                checkpoint_run_version=2, page_count=1, stored_bytes=len(raw.encode("utf-8")),
                row_count=state["rowsRead"], finished=True, checkpoint_json=canonical(state),
                updated_at=timezone.now())
            m.AiBusinessEvidenceRun.objects.filter(pk=parent.pk).update(version=2,
                stored_bytes=len(raw.encode("utf-8")))
        with patch.object(transport, "catalog", return_value=[tool(daily.INITIAL_TOOL)]), \
                patch.object(transport, "execute_tool", side_effect=self.signed_page):
            daily.advance_daily_source(self.run_id, "sales", 2, self.principal, "seal-sales")
        with self.assertRaises(AiError): seal.finish(self.run_id, 3, self.principal)
        self.assertEqual(m.AiBusinessEvidenceRun.objects.get(pk=self.run_id).status, "collecting")

    def test_legacy_signed_http_never_exposes_internal_v3_before_or_after_seal(self):
        def assert_hidden():
            root = "/api/ai/business-evidence"
            url = f"{root}/{self.run_id}"
            body = json.dumps({"expectedVersion": 3, "action": "seal"}, separators=(",", ":"))
            with patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}), \
                    override_settings(DJANGO_INTERNAL_SECRET=TEST_SECRET,
                                      DJANGO_PROCESS_ROLE="ai_reader", DJANGO_ENVIRONMENT="test"), \
                    patch("ai_assistant.views.authority"):
                listing = self.client.get(root, headers=signed_headers(root, email=self.principal.email))
                self.assertEqual(listing.status_code, 200, listing.content)
                self.assertNotIn(self.run_id, listing.content.decode())
                detail = self.client.get(url, headers=signed_headers(url, email=self.principal.email))
                self.assertEqual(detail.status_code, 404, detail.content)
                finish_url = url + "/finish"
                denied = self.client.post(finish_url, data=body, content_type="application/json",
                    headers=signed_headers(finish_url, email=self.principal.email, method="POST", body=body))
                self.assertEqual(denied.status_code, 403, denied.content)
            with self.assertRaises(AiError) as rejected:
                legacy.finish(self.run_id, {"expectedVersion": 3, "action": "seal"}, self.principal)
            self.assertEqual(rejected.exception.status, 404)
        assert_hidden()
        self.collect(); seal.finish(self.run_id, 3, self.principal)
        assert_hidden()

    def test_internal_v3_report_candidate_is_read_only_and_monthly_context_only(self):
        self.collect()
        sealed = seal.finish(self.run_id, 3, self.principal)
        request = {"schemaVersion": candidate.REQUEST_SCHEMA,
            "executionProfile": "business-agent-reference-v3-candidate",
            "evidenceRunId": self.run_id, "expectedEvidenceVersion": sealed["version"],
            "expectedSealDigest": sealed["seal"]["sealedDigest"]}
        with patch.object(transport, "execute_tool") as remote:
            result = candidate.prepare(request, self.principal)
            remote.assert_not_called()
        value = result["candidate"]
        self.assertEqual(value["reference"]["sealedDigest"], request["expectedSealDigest"])
        self.assertEqual(len(value["financeMonthlyContext"]), 1)
        self.assertEqual(value["financeMonthlyContext"][0]["role"], "monthly_context")
        self.assertEqual(len(value["dailyFacts"]), 1)
        self.assertFalse(value["policy"]["financeSkuProfitAttributionAllowed"])
        self.assertFalse(value["policy"]["sumOverlappingErpB2bAdsAllowed"])
        self.assertFalse(value["modelDispatchSupported"])
        self.assertFalse(m.AiReportRun.objects.exists())

    def test_candidate_rejects_wrong_profile_stale_seal_illicit_calculation_and_actor(self):
        self.collect()
        sealed = seal.finish(self.run_id, 3, self.principal)
        request = {"schemaVersion": candidate.REQUEST_SCHEMA,
            "executionProfile": "business-agent-reference-v3-candidate",
            "evidenceRunId": self.run_id, "expectedEvidenceVersion": sealed["version"],
            "expectedSealDigest": sealed["seal"]["sealedDigest"]}
        for change in ({"executionProfile": "business-agent-reference-v2"},
                       {"expectedEvidenceVersion": sealed["version"] - 1},
                       {"expectedSealDigest": "0" * 64},
                       {"financeSkuProfit": True},
                       {"sumOverlappingErpB2bAds": True}):
            with self.subTest(change=change), self.assertRaises(AiError):
                candidate.prepare({**request, **change}, self.principal)
        with transaction.atomic():
            self.actor.status = "inactive"; self.actor.save(update_fields=["status"])
            with self.assertRaises(AiError): candidate.prepare(request, self.principal)
            transaction.set_rollback(True)
        old = legacy.create({"schemaVersion": "business-evidence-v2", "clientRequestId": uid("v2"),
            "sources": [{"key": "sales", "domain": "sales", "query": self.daily_query}],
            "analysisRequest": {"schemaVersion": "business-analysis-request-v1",
                "question": "旧证据", "requestedDimensions": ["shop"],
                "requestedWindows": ["current"]}}, self.principal)
        with self.assertRaises(AiError):
            candidate.prepare({**request, "evidenceRunId": old["item"]["id"]}, self.principal)

    def test_inert_report_intent_is_paused_idempotent_and_has_no_jobs(self):
        self.collect()
        sealed = seal.finish(self.run_id, 3, self.principal)
        request = {"schemaVersion": intent.REQUEST_SCHEMA, "clientRequestId": "v3-intent-one",
            "executionProfile": "business-agent-reference-v3-candidate",
            "evidenceRunId": self.run_id, "expectedEvidenceVersion": sealed["version"],
            "expectedSealDigest": sealed["seal"]["sealedDigest"]}
        with patch.object(transport, "execute_tool") as remote:
            first = intent.create(request, self.principal)
            replayed = intent.create(request, self.principal)
            loaded = intent.inspect(first["item"]["id"], self.principal)
            remote.assert_not_called()
        self.assertFalse(first["replayed"])
        self.assertTrue(replayed["replayed"])
        self.assertEqual(first["item"], replayed["item"])
        self.assertEqual(first["item"]["status"], "paused")
        self.assertEqual(first["item"]["pauseReason"], "v3_agents_not_registered")
        self.assertIsNone(first["item"]["workflowRunId"])
        self.assertEqual(loaded["candidate"]["reference"]["sealedDigest"], request["expectedSealDigest"])
        self.assertEqual(loaded["workflowPlan"]["nodes"][-1]["key"], "human_review")
        self.assertTrue(loaded["workflowPlan"]["humanReviewRequired"])
        self.assertEqual(m.AiBusinessV3ReportIntent.objects.count(), 1)
        self.assertFalse(m.AiReportRun.objects.exists())
        self.assertFalse(m.AiWorkflowRuns.objects.exists())
        self.assertFalse(m.AiAgentJobs.objects.exists())
        self.assertFalse(m.AiAgentProviderDispatches.objects.exists())
        self.assertFalse(m.AiAgentToolResults.objects.exists())
        row = m.AiBusinessV3ReportIntent.objects.get(pk=first["item"]["id"])
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessV3ReportIntent.objects.filter(pk=row.pk).update(status="queued")
        with self.assertRaises(DatabaseError), transaction.atomic():
            row.delete()

    def test_intent_rejects_stale_actor_profile_parent_race_and_direct_forgery(self):
        self.collect()
        sealed = seal.finish(self.run_id, 3, self.principal)
        body = {"schemaVersion": intent.REQUEST_SCHEMA, "clientRequestId": "v3-intent-two",
            "executionProfile": "business-agent-reference-v3-candidate",
            "evidenceRunId": self.run_id, "expectedEvidenceVersion": sealed["version"],
            "expectedSealDigest": sealed["seal"]["sealedDigest"]}
        with self.assertRaises(AiError): intent.create({**body, "executionProfile": "business-agent-reference-v2"}, self.principal)
        with self.assertRaises(AiError): intent.create({**body, "expectedSealDigest": "0" * 64}, self.principal)
        original = intent.authorize_owner
        def changed_after_preparation(row, actor):
            current = original(row, actor)
            current.state_json = "{}"  # exact locked parent differs from preverified seal
            return current
        with patch.object(intent, "authorize_owner", side_effect=changed_after_preparation):
            with self.assertRaises(AiError): intent.create(body, self.principal)
        self.assertFalse(m.AiBusinessV3ReportIntent.objects.exists())
        with transaction.atomic():
            self.actor.status = "inactive"; self.actor.save(update_fields=["status"])
            with self.assertRaises(AiError): intent.create(body, self.principal)
            transaction.set_rollback(True)
        good = intent.create(body, self.principal)
        row = m.AiBusinessV3ReportIntent.objects.get(pk=good["item"]["id"])
        clone = {field: getattr(row, field) for field in ("owner_email", "scope_json",
            "request_digest", "evidence_run", "evidence_version", "sealed_digest",
            "candidate_digest", "snapshot_digest", "snapshot_json", "workflow_input_digest",
            "workflow_input_json", "workflow_plan_digest", "workflow_plan_json", "status", "pause_reason")}
        for changed in ({"status": "queued"}, {"pause_reason": "approved"},
                        {"snapshot_digest": "0" * 64}, {"workflow_plan_digest": "0" * 64}):
            with self.subTest(changed=changed), self.assertRaises(DatabaseError), transaction.atomic():
                m.AiBusinessV3ReportIntent.objects.create(id=uid("intent"),
                    client_request_id=uid("forged"), **{**clone, **changed})
        self.assertEqual(m.AiBusinessV3ReportIntent.objects.count(), 1)

    def test_prepared_v3_source_handles_are_source_bound_and_read_only(self):
        self.collect()
        sealed = seal.finish(self.run_id, 3, self.principal)
        created = intent.create({"schemaVersion": intent.REQUEST_SCHEMA,
            "clientRequestId": "prepared-reader-intent", "executionProfile": "business-agent-reference-v3-candidate",
            "evidenceRunId": self.run_id, "expectedEvidenceVersion": sealed["version"],
            "expectedSealDigest": sealed["seal"]["sealedDigest"]}, self.principal)["item"]
        before = (m.AiBusinessEvidenceChunk.objects.count(),
                  m.AiBusinessSourceToolReceipt.objects.count(),
                  m.AiToolAuditLogs.objects.count(), m.AiWriteReceipt.objects.count(),
                  m.AiMutationAudit.objects.count())
        with override_settings(DJANGO_INTERNAL_SECRET=TEST_SECRET):
            directory = source_read.directory(created["id"], {"offset": 0}, self.principal)
            self.assertTrue(directory["fullSealVerifiedForHandle"])
            self.assertFalse(directory["agentReadReceiptRecorded"])
            self.assertLessEqual(len(canonical(directory).encode("utf-8")), 38_000)
            keys = {item["sourceKey"]: item for item in directory["items"]}
            sales = keys["sales"]
            # Reload simulates a different process: validity comes from HMAC
            # and current immutable DB rows, never an in-memory handle cache.
            importlib.reload(source_read)
            page = source_read.page(created["id"], "sales", {"handle": sales["sourceHandle"],
                "sequence": 1, "rowOffset": 0, "rowLimit": 1}, self.principal)
            self.assertEqual(page["rows"][0]["shopName"], "测试店")
            self.assertEqual(page["domain"], "sales")
            self.assertEqual(page["chunkDigest"], digest(canonical(self.daily_page)))
            self.assertFalse(page["agentReadReceiptRecorded"])
            self.assertLessEqual(len(canonical(page).encode("utf-8")), 38_000)
            with self.assertRaises(AiError):
                source_read.page(created["id"], "finance", {"handle": sales["sourceHandle"],
                    "sequence": 1}, self.principal)
            with patch.object(source_read.time, "time", return_value=time.time() + 601):
                with self.assertRaises(AiError):
                    source_read.page(created["id"], "sales", {"handle": sales["sourceHandle"],
                        "sequence": 1}, self.principal)
            actual_context = source_read._context
            checks = []
            def revoke_before_final_fence(*args):
                checks.append(1)
                if len(checks) == 2:
                    AppUser.objects.filter(email=self.principal.email).update(status="inactive")
                return actual_context(*args)
            with transaction.atomic():
                with patch.object(source_read, "_context", side_effect=revoke_before_final_fence):
                    with self.assertRaises(AiError):
                        source_read.page(created["id"], "sales", {"handle": sales["sourceHandle"],
                            "sequence": 1}, self.principal)
                self.assertEqual(len(checks), 2)
                transaction.set_rollback(True)
            with patch.object(intent, "inspect", side_effect=AssertionError("full replay on continuation")):
                again = source_read.directory(created["id"], {"offset": 1,
                    "handle": directory["handle"]}, self.principal)
                self.assertEqual(again["offset"], 1)
            with patch.object(source_read, "MAX_PREPARED_PAGES", 0):
                with self.assertRaises(AiError) as cap:
                    source_read.directory(created["id"], {"offset": 0}, self.principal)
                self.assertEqual(cap.exception.status, 413)
            other = Principal("other-v3-reader@example.test", "Other", "admin", None)
            AppUser.objects.create(email=other.email, display_name="Other",
                role=AccessRole.objects.get(code="admin"), status="active", scope=None,
                version=1, created_at=timezone.now(), updated_at=timezone.now())
            with patch.object(source_read, "MAX_PREPARED_BYTES", 0):
                with self.assertRaises(AiError) as denied:
                    source_read.directory(created["id"], {"offset": 0}, other)
                self.assertEqual(denied.exception.status, 404)
        after = (m.AiBusinessEvidenceChunk.objects.count(),
                 m.AiBusinessSourceToolReceipt.objects.count(),
                 m.AiToolAuditLogs.objects.count(), m.AiWriteReceipt.objects.count(),
                 m.AiMutationAudit.objects.count())
        self.assertEqual(after, before)

    def test_signed_v3_reader_post_uses_writer_process_without_write(self):
        self.collect()
        sealed = seal.finish(self.run_id, 3, self.principal)
        created = intent.create({"schemaVersion": intent.REQUEST_SCHEMA,
            "clientRequestId": "signed-reader-intent", "executionProfile": "business-agent-reference-v3-candidate",
            "evidenceRunId": self.run_id, "expectedEvidenceVersion": sealed["version"],
            "expectedSealDigest": sealed["seal"]["sealedDigest"]}, self.principal)["item"]
        url = f"/api/ai/business-v3-source-read/{created['id']}/directory"
        body = json.dumps({"offset": 0}, separators=(",", ":"))
        before = (m.AiWriteReceipt.objects.count(), m.AiMutationAudit.objects.count(),
                  m.AiToolAuditLogs.objects.count())
        with patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}), \
                override_settings(DJANGO_INTERNAL_SECRET=TEST_SECRET, DJANGO_PROCESS_ROLE="ai_writer",
                                  DJANGO_ENVIRONMENT="test"), patch("ai_assistant.views.authority"):
            response = self.client.post(url, data=body, content_type="application/json",
                headers=signed_headers(url, email=self.principal.email, method="POST", body=body))
            self.assertEqual(response.status_code, 200, response.content)
            value = response.json()
            self.assertEqual(value["schemaVersion"], "business-v3-source-directory-v1")
            self.assertTrue(value["readOnlyOperationOnWriterProcess"])
        with patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}), \
                override_settings(DJANGO_INTERNAL_SECRET=TEST_SECRET, DJANGO_PROCESS_ROLE="ai_reader",
                                  DJANGO_ENVIRONMENT="test"), patch("ai_assistant.views.authority"):
            denied = self.client.post(url, data=body, content_type="application/json",
                headers=signed_headers(url, email=self.principal.email, method="POST", body=body))
            self.assertEqual(denied.status_code, 403)
        self.assertEqual((m.AiWriteReceipt.objects.count(), m.AiMutationAudit.objects.count(),
                          m.AiToolAuditLogs.objects.count()), before)

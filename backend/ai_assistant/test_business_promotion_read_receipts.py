"""Persisted fourth-tool proof with real owning page and exact row replay."""
import json
from unittest.mock import patch

from django import test as djtest

from . import business_promotion_read_receipts as service
from . import business_promotion_agent_tool as agent_tool
from . import models as m
from . import test_business_promotion_agent_tool as fixtures
from .policy import AiError, canonical, digest, mutation, uid


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionReadReceiptTests(djtest.TransactionTestCase):
    user = fixtures.PromotionAgentToolTests.user
    call = fixtures.PromotionAgentToolTests.call
    collect_body = fixtures.PromotionAgentToolTests.collect_body
    bundle = fixtures.PromotionAgentToolTests.bundle
    input_for = fixtures.PromotionAgentToolTests.input_for
    insert = fixtures.PromotionAgentToolTests.insert
    seed = fixtures.PromotionAgentToolTests.seed
    setUp = fixtures.PromotionAgentToolTests.setUp
    request_body = fixtures.PromotionAgentToolTests.request_body
    current_catalog = fixtures.PromotionAgentToolTests.current_catalog
    create_report = fixtures.PromotionAgentToolTests.create_report
    actual_job = fixtures.PromotionAgentToolTests.actual_job
    read = fixtures.PromotionAgentToolTests.read

    def progress(self, job):
        with patch.object(service.runtime.transport, "catalog", side_effect=self.current_catalog):
            return service.progress(job, self.admin)

    def append(self, job, args, data=None, *, state="succeeded", provider_args=None):
        ordinal = m.AiAgentToolDispatches.objects.filter(job=job).count()+1
        name = service.contract.PROMOTION_TOOL
        call_id = "promotion-call-"+str(ordinal)
        provider_call = {"id":call_id, "name":name, "arguments": provider_args or args}
        response = {"text":"", "calls":[provider_call], "frame":{"role":"assistant"}}
        with mutation(self.admin):
            provider = m.AiAgentProviderDispatches.objects.create(id=uid("promotion-provider"),
                job=job, dispatch_ordinal=ordinal, owner_email=job.owner_email,
                actor_role="admin", model_id=job.model_id, model_version=job.model_version,
                tool_policy_digest=job.tool_policy_digest, request_digest=digest(args),
                lease_epoch=job.lease_epoch, state="succeeded")
            m.AiAgentProviderResults.objects.create(dispatch=provider,
                response_json=canonical(response), response_digest=digest(response))
            dispatch = m.AiAgentToolDispatches.objects.create(id=uid("promotion-tool"),
                job=job, provider_dispatch=provider, tool_call_ordinal=ordinal,
                provider_call_id=call_id, tool_name=name, arguments_json=canonical(args),
                arguments_digest=digest(args), invocation_id=uid("promotion-invocation"),
                lease_epoch=job.lease_epoch, state=state)
            if data is not None:
                result = {"toolName":name, "ok":True, "auditStatus":"recorded", "data":data}
                m.AiAgentToolResults.objects.create(tool_dispatch=dispatch,
                    result_json=canonical(result), result_digest=digest(result))
            m.AiAgentJobs.objects.filter(pk=job.id).update(
                provider_round_count=ordinal, tool_call_count=ordinal)
        job.refresh_from_db()
        return dispatch

    def test_exact_saved_page_and_row_are_replayed_without_full_agent_grant(self):
        report = self.create_report()
        job = self.actual_job(report)
        args = {"reportId":report.id, "sourceKey":"ads", "view":"keyword_sku", "offset":0}
        page = self.read(job, args)
        self.append(job, args, page)
        row = page["table"]["rows"][0]
        row_args = {"reportId":report.id, "sourceKey":"ads", "view":"keyword_sku",
            "rowIndex":row["rowIndex"], "rowId":row["id"]}
        self.append(job, row_args, self.read(job, row_args))
        with patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote:
            proof = self.progress(job)
        self.assertEqual(proof["jobId"], job.id)
        self.assertEqual(proof["role"], "promotion")
        self.assertEqual(proof["promotionToolCalls"], 2)
        self.assertEqual(proof["views"]["keyword_sku"]["pages"], 1)
        self.assertEqual(proof["views"]["keyword_sku"]["rowReceipts"], 1)
        self.assertIn({"rowIndex":row["rowIndex"],"rowId":row["id"],"via":"row",
            "tableBindingDigest":page["binding"]["tableBindingDigest"]},
            proof["views"]["keyword_sku"]["seenRows"])
        self.assertFalse(proof["agentReadVerified"])
        self.assertFalse(proof["fullAgentReadComplete"])
        self.assertEqual(proof["views"]["keyword_sku_context"]["pages"], 0)
        model.assert_not_called(); remote.assert_not_called()

    def test_missing_first_page_and_unknown_dispatch_fail_closed(self):
        report = self.create_report()
        job = self.actual_job(report)
        args = {"reportId":report.id,"sourceKey":"ads","view":"keyword_sku","offset":1}
        self.append(job, args, self.read(job, args))
        with self.assertRaises(AiError): self.progress(job)
        other = self.create_report()
        pending = self.actual_job(other)
        second = {"reportId":other.id,"sourceKey":"ads","view":"keyword_sku_context"}
        self.append(pending, second, state="calling")
        with self.assertRaises(AiError) as caught: self.progress(job)
        self.assertEqual(caught.exception.code, "promotion_read_incomplete")
        with self.assertRaises(AiError) as pending_error: self.progress(pending)
        self.assertEqual(pending_error.exception.code, "tool_dispatch_unknown")

    def test_model_call_mismatch_and_replayed_fact_mismatch_are_rejected(self):
        report = self.create_report()
        job = self.actual_job(report)
        args = {"reportId":report.id,"sourceKey":"ads","view":"keyword_sku"}
        page = self.read(job, args)
        self.append(job, args, page, provider_args={**args, "view":"keyword_sku_context"})
        with self.assertRaises(AiError): self.progress(job)
        report2 = self.create_report()
        job2 = self.actual_job(report2)
        args2 = {**args, "reportId":report2.id}
        forged = json.loads(canonical(self.read(job2, args2)))
        forged["table"]["rows"][0]["rowIndex"] += 1
        self.append(job2, args2, forged)
        with self.assertRaises(AiError): self.progress(job2)

    def test_other_role_cannot_claim_promotion_receipts(self):
        report = self.create_report()
        job = self.actual_job(report, role="commerce")
        with self.assertRaises(AiError): self.progress(job)

    def test_checkpointed_queued_job_is_not_an_executing_read_proof(self):
        report = self.create_report()
        job = self.actual_job(report)
        args = {"reportId":report.id,"sourceKey":"ads","view":"keyword_sku"}
        self.append(job, args, self.read(job, args))
        with mutation(self.admin):
            m.AiAgentJobs.objects.filter(pk=job.id).update(status="queued", phase="queued",
                lease_token="", lease_expires_at=None)
        with self.assertRaises(AiError): self.progress(job)

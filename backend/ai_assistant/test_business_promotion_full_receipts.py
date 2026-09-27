"""One running Agent's four-tool persisted proof; no model dispatch."""
import json
from unittest.mock import patch

from django import test as djtest

from . import business_promotion_full_receipts as service
from . import business_promotion_agent_tool as fourth_tool
from . import business_promotion_tools as first_tools
from . import models as m
from . import test_business_promotion_tools as fixtures
from .policy import AiError, canonical, digest, mutation, uid


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionFullReceiptTests(djtest.TransactionTestCase):
    user = fixtures.PromotionModelToolsTests.user
    call = fixtures.PromotionModelToolsTests.call
    collect_body = fixtures.PromotionModelToolsTests.collect_body
    bundle = fixtures.PromotionModelToolsTests.bundle
    input_for = fixtures.PromotionModelToolsTests.input_for
    insert = fixtures.PromotionModelToolsTests.insert
    seed = fixtures.PromotionModelToolsTests.seed
    setUp = fixtures.PromotionModelToolsTests.setUp
    request_body = fixtures.PromotionModelToolsTests.request_body
    current_catalog = fixtures.PromotionModelToolsTests.current_catalog
    create_fixed_report = fixtures.PromotionModelToolsTests.create_fixed_report
    actual_job = fixtures.PromotionModelToolsTests.actual_job
    base = fixtures.PromotionModelToolsTests.base

    def read(self, job, name, args):
        with patch.object(first_tools.runtime.transport, "catalog", side_effect=self.current_catalog):
            if name == service.contract.PROMOTION_TOOL:
                return fourth_tool.read(job.id, args, self.admin)
            operation = {service.contract.PACKAGE_TOOL:"package",
                service.contract.TABLE_TOOL:"analysis",
                service.contract.BUDGET_TOOL:"budget"}[name]
            return first_tools.read(job.id, operation, args, self.admin)

    def progress(self, job):
        with patch.object(first_tools.runtime.transport, "catalog", side_effect=self.current_catalog):
            return service.progress(job, self.admin)

    def append(self, job, name, args, value=None, *, state="succeeded"):
        ordinal = m.AiAgentToolDispatches.objects.filter(job=job).count()+1
        call_id = "full-read-call-"+str(ordinal)
        provider_result = {"text":"", "calls":[{"id":call_id,"name":name,"arguments":args}],
            "frame":{"role":"assistant"}}
        with mutation(self.admin):
            provider = m.AiAgentProviderDispatches.objects.create(id=uid("full-read-provider"),
                job=job, dispatch_ordinal=ordinal, owner_email=job.owner_email, actor_role="admin",
                model_id=job.model_id, model_version=job.model_version,
                tool_policy_digest=job.tool_policy_digest, request_digest=digest(args),
                lease_epoch=job.lease_epoch, state="succeeded")
            m.AiAgentProviderResults.objects.create(dispatch=provider,
                response_json=canonical(provider_result), response_digest=digest(provider_result))
            dispatch = m.AiAgentToolDispatches.objects.create(id=uid("full-read-tool"),
                job=job, provider_dispatch=provider, tool_call_ordinal=ordinal,
                provider_call_id=call_id, tool_name=name, arguments_json=canonical(args),
                arguments_digest=digest(args), invocation_id=uid("full-read-invocation"),
                lease_epoch=job.lease_epoch, state=state)
            if value is not None:
                result = {"toolName":name,"ok":True,"auditStatus":"recorded","data":value}
                m.AiAgentToolResults.objects.create(tool_dispatch=dispatch,
                    result_json=canonical(result), result_digest=digest(result))
            m.AiAgentJobs.objects.filter(pk=job.id).update(
                provider_round_count=ordinal, tool_call_count=ordinal)
        job.refresh_from_db()
        return dispatch

    def package(self, report, job):
        base, offset = self.base(report), 0
        while offset is not None:
            args = {**base,"role":job.workflow_node_key,"offset":offset}
            page = self.read(job, service.contract.PACKAGE_TOOL, args)
            self.append(job, service.contract.PACKAGE_TOOL, args, page)
            offset = page["pagination"]["nextOffset"]

    def promotion(self, report, job):
        args = {"reportId":report.id,"sourceKey":"ads","view":"keyword_sku"}
        page = self.read(job, service.contract.PROMOTION_TOOL, args)
        self.append(job, service.contract.PROMOTION_TOOL, args, page)
        return page

    def test_complete_package_and_fourth_view_are_one_agent_proof_only(self):
        report = self.create_fixed_report()
        job = self.actual_job(report, role="promotion")
        self.package(report, job)
        before = self.progress(job)
        self.assertTrue(before["package"]["complete"])
        self.assertTrue(before["fullAgentReadComplete"])
        self.assertEqual(before["promotion"]["promotionToolCalls"], 0)
        self.assertFalse(before["promotionClaimsValidated"])
        self.assertTrue(before["optionalAnalysisAllComplete"])
        self.promotion(report, job)
        with patch("ai_assistant.provider.turn") as paid, patch(
                "ai_assistant.transport.execute_tool") as remote:
            proof = self.progress(job)
        self.assertTrue(proof["fullAgentReadComplete"])
        self.assertTrue(proof["agentReadVerified"])
        self.assertFalse(proof["allAgentsReadVerified"])
        self.assertFalse(proof["runtimeAdmissionGranted"])
        self.assertEqual(proof["promotion"]["promotionToolCalls"], 1)
        self.assertEqual(proof["toolCounts"][service.contract.PACKAGE_TOOL],
            proof["package"]["pages"])
        paid.assert_not_called(); remote.assert_not_called()

    def test_budget_is_required_for_promotion_role_and_replayed_to_end(self):
        report = self.create_fixed_report(budget=True)
        job = self.actual_job(report, role="promotion")
        self.package(report, job)
        self.promotion(report, job)
        self.assertFalse(self.progress(job)["fullAgentReadComplete"])
        offset = 0
        while offset is not None:
            args = {**self.base(report),"offset":offset}
            page = self.read(job, service.contract.BUDGET_TOOL, args)
            self.append(job, service.contract.BUDGET_TOOL, args, page)
            offset = page["budget"]["pagination"]["nextOffset"]
        proof = self.progress(job)
        self.assertTrue(proof["budget"]["required"])
        self.assertTrue(proof["budget"]["complete"])
        self.assertTrue(proof["fullAgentReadComplete"])

    def test_provider_and_later_tool_lease_epochs_are_monotonic_not_equal(self):
        report = self.create_fixed_report()
        job = self.actual_job(report, role="promotion")
        with mutation(self.admin):
            m.AiAgentJobs.objects.filter(pk=job.id).update(lease_epoch=2)
        job.refresh_from_db()
        self.package(report, job)
        provider = m.AiAgentProviderDispatches.objects.filter(job_id=job.id).order_by("dispatch_ordinal").first()
        with mutation(self.admin):
            m.AiAgentProviderDispatches.objects.filter(pk=provider.id).update(lease_epoch=1)
        self.assertTrue(self.progress(job)["package"]["complete"])
        with mutation(self.admin):
            m.AiAgentProviderDispatches.objects.filter(pk=provider.id).update(lease_epoch=3)
            m.AiAgentJobs.objects.filter(pk=job.id).update(lease_epoch=3)
        job.refresh_from_db()
        with self.assertRaises(AiError): self.progress(job)  # tool epoch 2 earlier than provider 3
        with mutation(self.admin):
            m.AiAgentProviderDispatches.objects.filter(pk=provider.id).update(lease_epoch=1)
            m.AiAgentJobs.objects.filter(pk=job.id).update(lease_epoch=1)
        job.refresh_from_db()
        with self.assertRaises(AiError): self.progress(job)  # tool epoch 2 future to job 1

    def test_package_offset_and_cross_role_result_fail_closed(self):
        report = self.create_fixed_report()
        job = self.actual_job(report, role="promotion")
        actual = self.read(job, service.contract.PACKAGE_TOOL,
            {**self.base(report),"role":"promotion","offset":0})
        wrong_offset = {**self.base(report),"role":"promotion","offset":1}
        self.append(job, service.contract.PACKAGE_TOOL, wrong_offset, actual)
        with self.assertRaises(AiError): self.progress(job)
        other = self.create_fixed_report()
        commerce = self.actual_job(other, role="commerce")
        args = {**self.base(other),"role":"commerce"}
        self.append(commerce, service.contract.PACKAGE_TOOL, args, actual)
        with self.assertRaises(AiError): self.progress(commerce)

    def test_unknown_tool_blocks_proof_without_replay(self):
        report = self.create_fixed_report()
        job = self.actual_job(report, role="promotion")
        args = {**self.base(report),"role":"promotion"}
        self.append(job, service.contract.PACKAGE_TOOL, args, state="calling")
        with self.assertRaises(AiError) as caught: self.progress(job)
        self.assertEqual(caught.exception.code, "tool_dispatch_unknown")

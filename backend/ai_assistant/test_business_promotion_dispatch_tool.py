"""Exact calling dispatch identity over real new-profile screening and Agent."""
from copy import deepcopy
import json
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from . import business_promotion_dispatch_tool as service
from . import business_promotion_runtime_contract as contract
from . import models as m
from . import test_business_promotion_agent_tool as fixtures
from .policy import AiError, canonical, digest, mutation, uid


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionDispatchToolTests(djtest.TransactionTestCase):
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

    def dispatch(self, job, name, arguments, *, call_id=None):
        ordinal = m.AiAgentProviderDispatches.objects.filter(job=job).count()+1
        call_id = call_id or "promotion-dispatch-call-"+str(ordinal)
        call = {"id":call_id, "name":name, "arguments":deepcopy(arguments)}
        payload = {"text":"", "calls":[call], "frame":{"role":"assistant","content":""}}
        with mutation(self.admin):
            provider = m.AiAgentProviderDispatches.objects.create(id=uid("promotion-model-call"),
                job=job, dispatch_ordinal=ordinal, owner_email=job.owner_email,
                actor_role="admin", model_id=job.model_id, model_version=job.model_version,
                tool_policy_digest=job.tool_policy_digest, request_digest=digest(arguments),
                lease_epoch=job.lease_epoch, state="succeeded")
            m.AiAgentProviderResults.objects.create(dispatch=provider,
                response_json=canonical(payload), response_digest=digest(payload))
            result = m.AiAgentToolDispatches.objects.create(id=uid("promotion-tool-dispatch"),
                job=job, provider_dispatch=provider, tool_call_ordinal=ordinal,
                provider_call_id=call_id, tool_name=name, arguments_json=canonical(arguments),
                arguments_digest=digest(arguments), invocation_id=uid("promotion-invocation"),
                lease_epoch=job.lease_epoch, state="calling")
            m.AiAgentJobs.objects.filter(pk=job.id).update(provider_round_count=ordinal)
        return result, call_id

    def bridge(self, dispatch, name, args, call_id):
        with patch.object(service.runtime.transport, "catalog", side_effect=self.current_catalog):
            return service.read(dispatch.id, name, args, call_id, self.admin)

    def test_package_and_fourth_tool_use_exact_saved_model_call_without_writes(self):
        report = self.create_report()
        job = self.actual_job(report)
        snapshot = json.loads(report.snapshot_json)
        package_args = {"runId":snapshot["evidenceRunId"], "reportId":report.id,
            "screeningId":snapshot["screeningIntent"]["id"], "role":"promotion", "offset":0}
        package, package_call = self.dispatch(job, contract.PACKAGE_TOOL, package_args)
        before = (m.AiAgentToolResults.objects.count(), m.AiAgentToolDispatches.objects.count())
        with patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as external, CaptureQueriesContext(connection) as queries:
            result = self.bridge(package, contract.PACKAGE_TOOL, package_args, package_call)
        self.assertEqual(result["role"], "promotion")
        self.assertEqual(result["reportId"], report.id)
        self.assertEqual(before, (m.AiAgentToolResults.objects.count(), m.AiAgentToolDispatches.objects.count()))
        model.assert_not_called(); external.assert_not_called()
        for query in queries:
            self.assertFalse(query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")))
        # The next current dispatch requires the previous known result. This
        # synthetic follow-up records exactly that result, without model calls.
        with mutation(self.admin):
            m.AiAgentToolResults.objects.create(tool_dispatch=package,
                result_json=canonical({"toolName":contract.PACKAGE_TOOL, "ok":True,
                    "auditStatus":"recorded", "data":result}),
                result_digest=digest({"toolName":contract.PACKAGE_TOOL, "ok":True,
                    "auditStatus":"recorded", "data":result}))
            package.state = "succeeded"; package.save(update_fields=["state"])
            m.AiAgentJobs.objects.filter(pk=job.id).update(tool_call_count=1)
        keyword_args = {"reportId":report.id, "sourceKey":"ads", "view":"keyword_sku"}
        keyword, keyword_call = self.dispatch(job, contract.PROMOTION_TOOL, keyword_args)
        page = self.bridge(keyword, contract.PROMOTION_TOOL, keyword_args, keyword_call)
        self.assertEqual(page["binding"]["reportBinding"]["reportId"], report.id)
        self.assertTrue(page["authority"]["reportBindingVerified"])

    def test_wrong_id_arguments_call_name_state_and_lease_fail_closed(self):
        report = self.create_report()
        job = self.actual_job(report)
        snapshot = json.loads(report.snapshot_json)
        args = {"runId":snapshot["evidenceRunId"], "reportId":report.id,
            "screeningId":snapshot["screeningIntent"]["id"], "role":"promotion"}
        dispatch, call_id = self.dispatch(job, contract.PACKAGE_TOOL, args)
        for changed in (("missing", contract.PACKAGE_TOOL, args, call_id),
                (dispatch.id, contract.PACKAGE_TOOL, {**args, "role":"commerce"}, call_id),
                (dispatch.id, contract.TABLE_TOOL, args, call_id),
                (dispatch.id, contract.PACKAGE_TOOL, args, "another-call")):
            with self.subTest(changed=changed[:2]), self.assertRaises(AiError):
                with patch.object(service.runtime.transport, "catalog", side_effect=self.current_catalog):
                    service.read(*changed, self.admin)
        with mutation(self.admin):
            m.AiAgentToolDispatches.objects.filter(pk=dispatch.id).update(state="unknown")
        with self.assertRaises(AiError): self.bridge(dispatch, contract.PACKAGE_TOOL, args, call_id)

    def test_expired_job_lease_rejects_saved_call_before_owning_read(self):
        report = self.create_report()
        job = self.actual_job(report)
        snapshot = json.loads(report.snapshot_json)
        args = {"runId":snapshot["evidenceRunId"], "reportId":report.id,
            "screeningId":snapshot["screeningIntent"]["id"], "role":"promotion"}
        dispatch, call_id = self.dispatch(job, contract.PACKAGE_TOOL, args)
        with mutation(self.admin):
            m.AiAgentJobs.objects.filter(pk=job.id).update(lease_expires_at=timezone.now())
        with (patch.object(service.tools, "read", side_effect=AssertionError("no owning read")) as owning,
                self.assertRaises(AiError)):
            self.bridge(dispatch, contract.PACKAGE_TOOL, args, call_id)
        owning.assert_not_called()

    def test_late_revocation_discards_owning_result(self):
        from access_control.models import AppUser
        report = self.create_report()
        job = self.actual_job(report)
        snapshot = json.loads(report.snapshot_json)
        args = {"runId":snapshot["evidenceRunId"], "reportId":report.id,
            "screeningId":snapshot["screeningIntent"]["id"], "role":"promotion"}
        dispatch, call_id = self.dispatch(job, contract.PACKAGE_TOOL, args)
        original = service.tools.read
        def revoked(*parts):
            result = original(*parts)
            AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            return result
        with patch.object(service.runtime.transport, "catalog", side_effect=self.current_catalog), patch.object(
                service.tools, "read", side_effect=revoked), self.assertRaises(AiError):
            service.read(dispatch.id, contract.PACKAGE_TOOL, args, call_id, self.admin)

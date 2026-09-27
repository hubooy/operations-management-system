"""Actual PostgreSQL payload hashes and late ledger changes, no model calls."""
import json
from unittest.mock import patch

from django import test as djtest
from django.db import connection, transaction, DatabaseError
from django.test.utils import CaptureQueriesContext

from . import business_screening_content_fence as service, business_screening_content as content
from . import business_budget_store, models as m, provider, transport
from . import test_business_screening_content as fixtures
from .business_sealed import Reader
from .policy import AiError, canonical, digest, mutation, uid


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class ScreeningContentFenceTests(djtest.TransactionTestCase):
    user = fixtures.ScreeningPreparedReviewTests.user
    call = fixtures.ScreeningPreparedReviewTests.call
    collect_body = fixtures.ScreeningPreparedReviewTests.collect_body
    bundle = fixtures.ScreeningPreparedReviewTests.bundle
    input_for = fixtures.ScreeningPreparedReviewTests.input_for
    insert = fixtures.ScreeningPreparedReviewTests.insert
    seed = fixtures.ScreeningPreparedReviewTests.seed
    setUp = fixtures.ScreeningPreparedReviewTests.setUp
    screening_bundle = fixtures.ScreeningPreparedReviewTests.screening_bundle
    insert_screening = fixtures.ScreeningPreparedReviewTests.insert_screening
    create_complete = fixtures.ScreeningPreparedReviewTests.create_complete
    waiting = fixtures.ScreeningPreparedReviewTests.waiting

    def first_tool(self, report):
        return m.AiAgentToolDispatches.objects.filter(job__workflow_run_id=report.workflow_id).order_by("job_id", "tool_call_ordinal").first()

    def test_real_small_fence_and_review_commit_never_load_facts_budget_or_network(self):
        report, _ = self.waiting(budget=True)
        prepared = content.prepare_review(report, self.admin)
        with patch.object(Reader, "pages", side_effect=AssertionError("no facts")), \
                patch.object(business_budget_store, "load", side_effect=AssertionError("no budget math")), \
                patch.object(transport, "catalog", side_effect=AssertionError("no network")), \
                patch.object(provider, "turn", side_effect=AssertionError("no model")), CaptureQueriesContext(connection) as queries:
            one = service.fence(report, self.admin)
            two = service.fence(report, self.admin)
            content.revalidate_review(prepared, self.admin)
        self.assertEqual(one, two)
        self.assertEqual(one["fenceDigest"], digest({k:v for k,v in one.items() if k != "fenceDigest"}))
        self.assertLess(len(canonical(one).encode()), 1024)
        self.assertTrue(any("sha256(convert_to" in row["sql"] for row in queries))
        self.assertFalse(any(row["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for row in queries))
        self.assertFalse(any("netshop_rows" in row["sql"] or "sales_order_lines" in row["sql"] for row in queries))

    def test_db_identity_guard_and_independent_read_faults_reject_changed_arguments(self):
        report, _ = self.waiting()
        prepared = content.prepare_review(report, self.admin)
        before = service.fence(report, self.admin)
        tool = self.first_tool(report)
        changed = canonical({**json.loads(tool.arguments_json), "offset": 1})
        cases = ({"arguments_json": changed}, {"arguments_json": changed, "arguments_digest": digest(changed)},
                 {"provider_call_id": "late-call"}, {"invocation_id": "late-invocation"})
        for values in cases:
            with self.subTest(values=tuple(values)):
                with self.assertRaisesRegex(DatabaseError, "ai_immutable_identity"):
                    with mutation(self.admin): m.AiAgentToolDispatches.objects.filter(pk=tool.id).update(**values)
        self.assertEqual(service.fence(report, self.admin), before)
        # DB identity is immutable. Independently inject a corrupt read result,
        # without altering the schema or claiming the forbidden UPDATE worked.
        original_rows = service._rows
        for resigned in (False, True):
            def injected(query, job_id):
                rows = original_rows(query, job_id)
                if query == service.TOOLS and job_id == tool.job_id:
                    rows = [list(row) for row in rows]
                    row = next(row for row in rows if row[0] == tool.id)
                    row[4], row[5], row[6] = digest(changed), len(changed.encode()), digest("changed row")
                    if resigned: row[3] = row[4]
                return rows
            with self.subTest(resigned=resigned), patch.object(service, "_rows", side_effect=injected):
                with self.assertRaises(AiError): content.revalidate_review(prepared, self.admin)
                if resigned: self.assertNotEqual(service.fence(report, self.admin), before)
                else:
                    with self.assertRaises(AiError): service.fence(report, self.admin)
        # State remains legitimately mutable and is part of the real SQL SHA.
        with mutation(self.admin): m.AiAgentToolDispatches.objects.filter(pk=tool.id).update(state="failed")
        self.assertNotEqual(service.fence(report, self.admin), before)
        with self.assertRaises(AiError): content.revalidate_review(prepared, self.admin)

    def test_content_preparation_catches_late_ledger_and_guidance_changes(self):
        report, _ = self.waiting()
        tool = self.first_tool(report)
        original = content.content
        def change_after_validation(*args, **kwargs):
            value = original(*args, **kwargs)
            with mutation(self.admin):
                m.AiAgentProviderDispatches.objects.filter(pk=tool.provider_dispatch_id).update(
                    request_digest=digest("changed-after-full-content"))
            return value
        with patch.object(content, "content", side_effect=change_after_validation), self.assertRaises(AiError):
            content.prepare_review(report, self.admin)
        prepared = content.prepare_review(report, self.admin)
        with mutation(self.admin):
            m.AiExecutionGuidance.objects.create(entity_id=tool.job_id,
                snapshot_json=canonical({"prompt":"late guidance", "guidance":{}, "skills":{}}))
        with self.assertRaises(AiError): content.revalidate_review(prepared, self.admin)

    def test_actual_provider_and_tool_result_sha_and_cross_job_parent_are_checked(self):
        report, _ = self.waiting()
        tool = self.first_tool(report)
        provider_row = m.AiAgentProviderDispatches.objects.get(pk=tool.provider_dispatch_id)
        # Existing fixtures explicitly author tool evidence, with no provider
        # output. Missing results are fingerprinted; present ones must be exact.
        with transaction.atomic():
            with mutation(self.admin):
                m.AiAgentProviderResults.objects.create(dispatch=provider_row,
                    response_json='{"text":"synthetic"}', response_digest="f"*64)
            with self.assertRaises(AiError): service.fence(report, self.admin)
            transaction.set_rollback(True)
        with transaction.atomic():
            with mutation(self.admin):
                last = m.AiAgentToolDispatches.objects.filter(job_id=tool.job_id).count()
                extra = m.AiAgentToolDispatches.objects.create(id=uid("fence-tool"),job_id=tool.job_id,
                    provider_dispatch=provider_row,tool_call_ordinal=last+1,provider_call_id="extra",tool_name=tool.tool_name,
                    arguments_json=tool.arguments_json,arguments_digest=tool.arguments_digest,
                    invocation_id=uid("fence-invocation"),state="failed",lease_epoch=1)
                m.AiAgentToolResults.objects.create(tool_dispatch=extra,result_json="{}",result_digest="f"*64)
            with self.assertRaises(AiError): service.fence(report, self.admin)
            transaction.set_rollback(True)
        other = m.AiAgentProviderDispatches.objects.filter(job__workflow_run_id=report.workflow_id).exclude(job_id=tool.job_id).first()
        with self.assertRaisesRegex(DatabaseError, "ai_immutable_identity"):
            with mutation(self.admin):
                m.AiAgentToolDispatches.objects.filter(pk=tool.id).update(provider_dispatch=other,provider_call_id="cross-job")
        original_rows = service._rows
        def wrong_parent(query, job_id):
            rows = original_rows(query, job_id)
            if query == service.TOOLS and job_id == tool.job_id:
                rows = [list(row) for row in rows]
                next(row for row in rows if row[0] == tool.id)[2] = other.id
            return rows
        with patch.object(service, "_rows", side_effect=wrong_parent), self.assertRaises(AiError):
            service.fence(report, self.admin)

    def test_current_owner_scope_and_disabled_account_are_revalidated(self):
        from access_control.models import AppUser
        report, _ = self.waiting()
        other = self.user("fence-other@example.invalid", "admin", None)
        with self.assertRaises(AiError): service.fence(report, other)
        AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        with self.assertRaises(AiError) as caught: service.fence(report, self.admin)
        self.assertEqual(caught.exception.status, 403)

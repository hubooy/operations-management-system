"""Real-role v10 download metadata fence; no public chunk route."""
import json
from unittest.mock import patch

from django import test as djtest
from django.db import connection
import psycopg

from access_control.models import AppUser
from business_analysis import promotion_budget_v10_download_fence as fence
from business_analysis import promotion_budget_v10_publish_request as publish_request
from business_analysis.contracts import canonical
from . import models as m
from . import test_business_promotion_budget_v10_attestation as fixture
from . import test_business_promotion_approved_content as approved_fixture


ATTESTOR = "teruisi_ai_budget_v10_attestor"
READER = "teruisi_ai_reader"
BODY = "public.ai_budget_v10_download_fence_body(%s,%s,%s,%s)"
READ = "public.ai_budget_v10_download_receipt(%s,%s,%s,%s)"
PUBLISH = "public.ai_budget_v10_publish(%s,%s,%s,%s,%s,%s)"


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BudgetV10ReaderFenceTests(djtest.TransactionTestCase):
    user = fixture.BudgetV10AttestationTests.user
    call = fixture.BudgetV10AttestationTests.call
    collect_body = fixture.BudgetV10AttestationTests.collect_body
    bundle = fixture.BudgetV10AttestationTests.bundle
    input_for = fixture.BudgetV10AttestationTests.input_for
    insert = fixture.BudgetV10AttestationTests.insert
    seed = fixture.BudgetV10AttestationTests.seed
    setUp = fixture.BudgetV10AttestationTests.setUp
    request_body = fixture.BudgetV10AttestationTests.request_body
    current_catalog = fixture.BudgetV10AttestationTests.current_catalog
    create_fixed_report = fixture.BudgetV10AttestationTests.create_fixed_report
    base = fixture.BudgetV10AttestationTests.base
    read = fixture.BudgetV10AttestationTests.read
    append = fixture.BudgetV10AttestationTests.append
    package = fixture.BudgetV10AttestationTests.package
    promotion = fixture.BudgetV10AttestationTests.promotion
    complete = fixture.BudgetV10AttestationTests.complete
    running_job = fixture.BudgetV10AttestationTests.running_job
    five_completed = fixture.BudgetV10AttestationTests.five_completed
    approved = fixture.BudgetV10AttestationTests.approved
    _complete_budget_report = fixture.BudgetV10AttestationTests._complete_budget_report
    _stage = fixture.BudgetV10AttestationTests._stage
    _database = staticmethod(fixture.BudgetV10AttestationTests._database)
    _body = staticmethod(fixture.BudgetV10AttestationTests._body)
    _attest = fixture.BudgetV10AttestationTests._call
    complete_flow = approved_fixture.PromotionApprovedContentTests.complete_flow

    def _role(self, role, signature, args):
        with self._database() as db:
            db.execute("SET SESSION AUTHORIZATION " + role)
            try:
                return db.execute("SELECT " + signature, args).fetchone()[0]
            finally:
                db.execute("RESET SESSION AUTHORIZATION")

    def _ready(self, *, budget=False, valid_fence=True):
        report = self._complete_budget_report() if budget else self.five_completed(
            promotion_reference=True)
        if not budget:
            self.approved(report)
        self.complete_flow(report)
        row = self._stage(report)
        candidate = self._body(row)
        if valid_fence:
            body = self._role(ATTESTOR, BODY, [row.id,
                candidate["fullManifestDigest"], candidate["budgetProofDigest"],
                candidate["approvedContentDigest"]])
            candidate["publicationFenceDigest"] = fence.fence_digest(body)
            with connection.cursor() as cursor:
                cursor.execute("SELECT public.ai_v4_replay_canonical(%s::jsonb)",
                    [canonical(body)])
                self.assertEqual(cursor.fetchone()[0], canonical(body))
        attestation_id = self._attest(row, candidate, as_role=True)
        receipt = m.AiBusinessPromotionBudgetV10Attestation.objects.get(
            pk=attestation_id)
        request_digest = publish_request.request_digest(run_id=row.id,
            attempt=row.attempt, expected_version=row.version,
            attestation_id=receipt.id,
            attestation_sha256=receipt.attestation_sha256,
            binding_digest=receipt.binding_digest,
            full_manifest_digest=receipt.full_manifest_digest,
            full_manifest_sha256=receipt.full_manifest_sha256)
        result = self._role(ATTESTOR, PUBLISH, [row.id, row.attempt,
            row.version, receipt.id, receipt.attestation_sha256, request_digest])
        self.assertEqual(result["status"], "committed")
        row.refresh_from_db()
        return report, row, receipt

    def test_reader_gets_only_narrow_current_ready_receipt(self):
        report, row, receipt = self._ready()
        with connection.cursor() as cursor:
            from importlib import import_module
            import_module(
                "ai_assistant.migrations.0059_business_promotion_budget_v10_reader_fence").verify_catalog(cursor)
            cursor.execute("SELECT has_table_privilege(%s,%s,'SELECT')",
                [READER, "public.ai_business_promotion_budget_v10_attestations"])
            self.assertEqual(cursor.fetchone(), (False,))
        info = self._role(READER, READ, [row.id, self.admin.email.lower(),
            row.attempt, row.binding_digest])
        self.assertEqual(info["schemaVersion"],
            "business-promotion-budget-v10-download-receipt-v1")
        self.assertEqual((info["runId"], info["attestationId"],
            info["manifestFileSha256"]),
            (row.id, receipt.id, receipt.full_manifest_sha256))
        self.assertFalse(info["budgetPresent"])
        with self.assertRaises(psycopg.Error):
            self._role(READER, BODY, [row.id, receipt.full_manifest_digest,
                receipt.budget_proof_digest, receipt.approved_content_digest])
        for args in ([row.id, "other@example.test", row.attempt, row.binding_digest],
                [row.id, self.admin.email.lower(), row.attempt+1, row.binding_digest],
                [row.id, self.admin.email.lower(), row.attempt, "0" * 64]):
            with self.assertRaises(psycopg.Error):
                self._role(READER, READ, args)
        AppUser.objects.filter(email=self.admin.email.lower()).update(
            status="disabled")
        with self.assertRaises(psycopg.Error):
            self._role(READER, READ, [row.id, self.admin.email.lower(),
                row.attempt, row.binding_digest])

    def test_budget_report_has_matching_current_fence(self):
        report, row, receipt = self._ready(budget=True)
        info = self._role(READER, READ, [row.id, self.admin.email.lower(),
            row.attempt, row.binding_digest])
        self.assertTrue(info["budgetPresent"])
        self.assertEqual(info["budgetPlanDigest"],
            json.loads(report.snapshot_json)["budgetRef"]["planDigest"])
        self.assertEqual(info["budgetProofDigest"], receipt.budget_proof_digest)

    def test_old_arbitrary_fence_ready_row_fails_closed(self):
        _, row, _ = self._ready(valid_fence=False)
        with self.assertRaises(psycopg.Error):
            self._role(READER, READ, [row.id, self.admin.email.lower(),
                row.attempt, row.binding_digest])

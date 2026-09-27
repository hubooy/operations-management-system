"""Isolated real-role 0057/0058 integration for the disabled v10 caller.

SESSION AUTHORIZATION is test-only on a disposable PostgreSQL database. The
attestor remains NOLOGIN; this creates no credential or application route.
"""
from unittest.mock import patch
import json

from django import test as djtest
from django.db import connection
from business_analysis.contracts import AnalysisContractError

from . import business_promotion_budget_v10_preflight as preflight
from . import business_promotion_budget_v10_publish_step as step
from . import business_promotion_budget_v10_stage as stage
from . import models as m
from . import test_business_promotion_budget_v10_publish_gate as fixture
from . import test_business_promotion_approved_content as approved_fixture


ROLE = "teruisi_ai_budget_v10_attestor"


class CountingCursor:
    def __init__(self, parent):
        self.parent = parent
        self.actual = None

    def __enter__(self):
        self.actual = self.parent.db.cursor()
        self.actual.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback):
        return self.actual.__exit__(exc_type, exc, traceback)

    def execute(self, sql, args):
        name = sql.split("public.", 1)[1].split("(", 1)[0]
        self.parent.calls.append(name)
        result = self.actual.execute(sql, args)
        if name == self.parent.lose_after and not self.parent.lost:
            self.parent.lost = True
            raise OSError("synthetic driver response lost after committed statement")
        return result

    def fetchmany(self, count):
        return self.actual.fetchmany(count)


class CountingConnection:
    autocommit = True

    def __init__(self, db, *, lose_after=None):
        self.db = db
        self.calls = []
        self.lose_after = lose_after
        self.lost = False

    def cursor(self):
        return CountingCursor(self)


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BudgetV10PublishStepRoleTests(djtest.TransactionTestCase):
    user = fixture.BudgetV10PublishGateTests.user
    call = fixture.BudgetV10PublishGateTests.call
    collect_body = fixture.BudgetV10PublishGateTests.collect_body
    bundle = fixture.BudgetV10PublishGateTests.bundle
    input_for = fixture.BudgetV10PublishGateTests.input_for
    insert = fixture.BudgetV10PublishGateTests.insert
    seed = fixture.BudgetV10PublishGateTests.seed
    setUp = fixture.BudgetV10PublishGateTests.setUp
    request_body = fixture.BudgetV10PublishGateTests.request_body
    current_catalog = fixture.BudgetV10PublishGateTests.current_catalog
    create_fixed_report = fixture.BudgetV10PublishGateTests.create_fixed_report
    base = fixture.BudgetV10PublishGateTests.base
    read = fixture.BudgetV10PublishGateTests.read
    append = fixture.BudgetV10PublishGateTests.append
    package = fixture.BudgetV10PublishGateTests.package
    promotion = fixture.BudgetV10PublishGateTests.promotion
    complete = fixture.BudgetV10PublishGateTests.complete
    running_job = fixture.BudgetV10PublishGateTests.running_job
    five_completed = fixture.BudgetV10PublishGateTests.five_completed
    approved = fixture.BudgetV10PublishGateTests.approved
    _complete_budget_report = fixture.BudgetV10PublishGateTests._complete_budget_report
    _stage = fixture.BudgetV10PublishGateTests._stage
    _database = staticmethod(fixture.BudgetV10PublishGateTests._database)
    complete_flow = approved_fixture.PromotionApprovedContentTests.complete_flow

    def _staged(self):
        report = self.five_completed(promotion_reference=True)
        self.approved(report)
        self.complete_flow(report)
        return self._stage(report)

    def _run(self, row, *, lose_after=None, expected=None):
        with self._database() as raw:
            raw.execute("SET SESSION AUTHORIZATION " + ROLE)
            wrapped = CountingConnection(raw, lose_after=lose_after)
            try:
                with patch.object(stage.approved_content.runtime.transport,
                        "catalog", side_effect=self.current_catalog):
                    result = step.attest_and_publish(wrapped, row.id, self.admin,
                        enabled=True, expected_preflight=expected)
            finally:
                raw.execute("RESET SESSION AUTHORIZATION")
        return result, wrapped.calls

    def test_real_preflight_one_attestation_one_publish_and_exact_outcome(self):
        row = self._staged()
        with connection.cursor() as cursor:
            cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
                "rolcreaterole,rolreplication,rolbypassrls "
                "FROM pg_catalog.pg_roles WHERE rolname=%s", [ROLE])
            self.assertEqual(cursor.fetchone(), (False,) * 7)
        with patch.object(stage.approved_content.runtime.transport,
                "catalog", side_effect=self.current_catalog):
            prepared = preflight.prepare(row.id, self.admin, enabled=True)
        result, calls = self._run(row, expected=prepared)
        self.assertEqual(calls, ["ai_budget_v10_attest_staged",
            "ai_budget_v10_publish", "ai_budget_v10_publish_outcome"])
        self.assertEqual(result["status"], "publication_recorded")
        self.assertFalse(result["authorityVerified"])
        self.assertFalse(result["readyAuthorized"])
        att = m.AiBusinessPromotionBudgetV10Attestation.objects.get(
            run_id=row.id, attempt=row.attempt)
        self.assertEqual(m.AiBusinessPromotionBudgetV10Attestation.objects.filter(
            run_id=row.id).count(), 1)
        self.assertEqual(att.id, result["attestationId"])
        self.assertEqual(att.attestation_sha256,
            prepared["attestationSha256"])
        row.refresh_from_db()
        self.assertEqual((row.status, row.attempt, row.version),
            ("ready", att.attempt, prepared["runVersion"] + 1))
        self.assertEqual(json.loads(row.progress_json)["publishRequestDigest"],
            result["requestDigest"])
        with self._database() as raw:
            raw.execute("SET SESSION AUTHORIZATION " + ROLE)
            wrapped = CountingConnection(raw)
            try:
                recovered = step.recover_publish_outcome(wrapped,
                    run_id=row.id, attempt=row.attempt,
                    expected_version=prepared["runVersion"],
                    attestation_id=att.id,
                    attestation_sha256=att.attestation_sha256,
                    binding_digest=att.binding_digest,
                    full_manifest_digest=att.full_manifest_digest,
                    full_manifest_sha256=att.full_manifest_sha256,
                    original_request_digest=result["requestDigest"],
                    enabled=True)
            finally:
                raw.execute("RESET SESSION AUTHORIZATION")
        self.assertEqual(wrapped.calls, ["ai_budget_v10_publish_outcome"])
        self.assertEqual(recovered["status"], "publication_recorded")
        self.assertFalse(recovered["authorityVerified"])

    def test_wrong_preflight_and_disabled_do_not_call_attestor(self):
        row = self._staged()
        with self._database() as raw:
            raw.execute("SET SESSION AUTHORIZATION " + ROLE)
            wrapped = CountingConnection(raw)
            try:
                with self.assertRaises(AnalysisContractError):
                    step.attest_and_publish(wrapped, row.id, self.admin)
                self.assertEqual(wrapped.calls, [])
            finally:
                raw.execute("RESET SESSION AUTHORIZATION")
        with patch.object(stage.approved_content.runtime.transport,
                "catalog", side_effect=self.current_catalog):
            prepared = preflight.prepare(row.id, self.admin, enabled=True)
        changed = {**prepared, "runVersion": prepared["runVersion"] + 1}
        with self.assertRaises(AnalysisContractError):
            self._run(row, expected=changed)
        self.assertFalse(m.AiBusinessPromotionBudgetV10Attestation.objects.filter(
            run_id=row.id).exists())
        row.refresh_from_db()
        self.assertEqual(row.status, "paused")

    def test_lost_0057_response_stops_without_0058_or_retry(self):
        row = self._staged()
        result, calls = self._run(row, lose_after="ai_budget_v10_attest_staged")
        self.assertEqual(result["status"], "unknown_attestation_result")
        self.assertEqual(calls, ["ai_budget_v10_attest_staged"])
        self.assertFalse(result["authorityVerified"])
        self.assertEqual(m.AiBusinessPromotionBudgetV10Attestation.objects.filter(
            run_id=row.id, attempt=row.attempt).count(), 1)
        row.refresh_from_db()
        self.assertEqual(row.status, "paused")

    def test_lost_0058_response_requires_explicit_outcome_only(self):
        row = self._staged()
        result, calls = self._run(row, lose_after="ai_budget_v10_publish")
        self.assertEqual(result["status"], "unknown_publish_result")
        self.assertEqual(calls, ["ai_budget_v10_attest_staged",
            "ai_budget_v10_publish"])
        self.assertFalse(result["authorityVerified"])
        row.refresh_from_db()
        self.assertEqual(row.status, "ready")
        with self._database() as raw:
            raw.execute("SET SESSION AUTHORIZATION " + ROLE)
            wrapped = CountingConnection(raw)
            try:
                recovered = step.recover_publish_outcome(wrapped,
                    run_id=result["runId"], attempt=result["attempt"],
                    expected_version=result["expectedVersion"],
                    attestation_id=result["attestationId"],
                    attestation_sha256=result["attestationSha256"],
                    binding_digest=result["bindingDigest"],
                    full_manifest_digest=result["fullManifestDigest"],
                    full_manifest_sha256=result["fullManifestSha256"],
                    original_request_digest=result["requestDigest"],
                    enabled=True)
            finally:
                raw.execute("RESET SESSION AUTHORIZATION")
        self.assertEqual(wrapped.calls, ["ai_budget_v10_publish_outcome"])
        self.assertEqual(recovered["status"], "publication_recorded")
        self.assertFalse(recovered["authorityVerified"])
        self.assertEqual(m.AiBusinessPromotionBudgetV10Attestation.objects.filter(
            run_id=row.id).count(), 1)

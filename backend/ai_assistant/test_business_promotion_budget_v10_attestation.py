"""Real-role closed v10 attestation; isolated PostgreSQL only."""
from copy import deepcopy
import hashlib
from importlib import import_module
import json
import os
from unittest.mock import patch

from django import test as djtest
from django.conf import settings
from django.db import DatabaseError, connection, transaction
import psycopg

from . import business_promotion_budget_v10_stage as stage
from . import models as m
from . import test_business_promotion_budget_v10_stage as fixture
from . import test_business_promotion_approved_content as approved_fixture
from .policy import canonical, digest, mutation


ROLE = "teruisi_ai_budget_v10_attestor"
SIGNATURE = "public.ai_budget_v10_attest_staged(text,integer,text)"


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BudgetV10AttestationTests(djtest.TransactionTestCase):
    user = fixture.PromotionBudgetV10StageTests.user
    call = fixture.PromotionBudgetV10StageTests.call
    collect_body = fixture.PromotionBudgetV10StageTests.collect_body
    bundle = fixture.PromotionBudgetV10StageTests.bundle
    input_for = fixture.PromotionBudgetV10StageTests.input_for
    insert = fixture.PromotionBudgetV10StageTests.insert
    seed = fixture.PromotionBudgetV10StageTests.seed
    setUp = fixture.PromotionBudgetV10StageTests.setUp
    request_body = fixture.PromotionBudgetV10StageTests.request_body
    current_catalog = fixture.PromotionBudgetV10StageTests.current_catalog
    create_fixed_report = fixture.PromotionBudgetV10StageTests.create_fixed_report
    base = fixture.PromotionBudgetV10StageTests.base
    read = fixture.PromotionBudgetV10StageTests.read
    append = fixture.PromotionBudgetV10StageTests.append
    package = fixture.PromotionBudgetV10StageTests.package
    promotion = fixture.PromotionBudgetV10StageTests.promotion
    complete = fixture.PromotionBudgetV10StageTests.complete
    running_job = fixture.PromotionBudgetV10StageTests.running_job
    five_completed = fixture.PromotionBudgetV10StageTests.five_completed
    approved = fixture.PromotionBudgetV10StageTests.approved
    _complete_budget_report = fixture.PromotionBudgetV10StageTests._complete_budget_report
    _stage = fixture.PromotionBudgetV10StageTests._stage
    complete_flow = approved_fixture.PromotionApprovedContentTests.complete_flow

    @staticmethod
    def _database():
        value = settings.DATABASES["default"]
        if (settings.DJANGO_ENVIRONMENT != "test" or
                value["HOST"] != "127.0.0.1" or
                not 55440 <= int(value["PORT"]) <= 55999 or
                str(value["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT") or
                value["NAME"] != "test_teruisi_ai_rehearsal"):
            raise AssertionError("role probe requires isolated PostgreSQL")
        return psycopg.connect(host=value["HOST"], port=value["PORT"],
            dbname=value["NAME"], user=value["USER"],
            password=value["PASSWORD"], autocommit=True)

    @staticmethod
    def _body(row):
        compact = json.loads(row.manifest_json)
        chunks = m.AiBusinessVolumeChunk.objects.filter(run=row,
            attempt=row.attempt, volume_index=0, format="json").order_by("sequence")
        raw = b"".join(bytes(part.content) for part in chunks)
        full = json.loads(raw)
        body = {"schemaVersion": "business-promotion-budget-v10-staged-attestation-v1",
            "runId": row.id, "attempt": row.attempt,
            "bindingDigest": row.binding_digest,
            "compactJsonSha256": hashlib.sha256(row.manifest_json.encode()).hexdigest(),
            "fullManifestSha256": hashlib.sha256(raw).hexdigest(),
            "fullManifestDigest": full["manifestDigest"],
            "files": {"files": compact["files"],
                "manifestFile": compact["manifestFile"]},
            "approvedContentDigest": full["promotionBudgetProof"]["approvedContentDigest"],
            "humanReviewDigest": full["promotionBudgetProof"]["humanReviewDigest"],
            "budgetPresent": row.report.budget_plan_id is not None,
            "budgetPlanDigest": full.get("budgetPlanDigest"),
            "budgetProofDigest": full["promotionBudgetProof"]["proofDigest"],
            # Isolated fixtures have no protected external verifier. These
            # opaque digests test role isolation, not publication authority.
            "owningVerificationDigest": digest(["isolated-candidate", row.id,
                full["manifestDigest"]]),
            "publicationFenceDigest": digest(["isolated-fence", row.id]),
            "verifierVersion": "business-promotion-budget-v10-owning-verifier-v1"}
        return body

    def _call(self, row, body, *, as_role):
        with self._database() as db:
            if as_role:
                db.execute("SET SESSION AUTHORIZATION " + ROLE)
            try:
                return db.execute("SELECT " + SIGNATURE.split("(", 1)[0] +
                    "(%s,%s,%s)", [row.id, row.attempt,
                    canonical(body)]).fetchone()[0]
            finally:
                if as_role:
                    db.execute("RESET SESSION AUTHORIZATION")

    def test_role_acl_is_default_closed_and_direct_write_denied(self):
        with connection.cursor() as cursor:
            import_module("ai_assistant.migrations.0057_business_promotion_budget_v10_attestation").verify_catalog(cursor)
            cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
                "rolcreaterole,rolreplication,rolbypassrls FROM pg_roles "
                "WHERE rolname=%s", [ROLE])
            self.assertEqual(cursor.fetchone(), (False,) * 7)
            cursor.execute("SELECT count(*) FROM pg_auth_members WHERE "
                "roleid=%s::regrole OR member=%s::regrole", [ROLE, ROLE])
            self.assertEqual(cursor.fetchone(), (0,))
            cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE'),"
                "has_table_privilege(%s,%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')",
                [ROLE, SIGNATURE, ROLE,
                    "public.ai_business_promotion_budget_v10_attestations"])
            self.assertEqual(cursor.fetchone(), (True, False))
        with self._database() as db:
            with self.assertRaises(psycopg.Error):
                db.execute("SELECT public.ai_budget_v10_attest_staged(%s,%s,%s)",
                    ["missing-run", 1, "{}"])

    def test_same_attempt_attests_only_under_role_and_rejects_forged_proof(self):
        report = self.five_completed(promotion_reference=True)
        self.approved(report)
        self.complete_flow(report)
        row = self._stage(report)
        body = self._body(row)
        forged = deepcopy(body)
        forged["budgetProofDigest"] = "0" * 64
        with self.assertRaises(psycopg.Error):
            self._call(row, body, as_role=False)
        with self.assertRaises(psycopg.Error):
            self._call(row, forged, as_role=True)
        forged = deepcopy(body)
        forged["files"]["files"][0]["sha256"] = "0" * 64
        with self.assertRaises(psycopg.Error):
            self._call(row, forged, as_role=True)
        first = self._call(row, body, as_role=True)
        self.assertEqual(first, self._call(row, body, as_role=True))
        receipt = m.AiBusinessPromotionBudgetV10Attestation.objects.get(
            run_id=row.id, attempt=row.attempt)
        self.assertEqual(receipt.id, first)
        self.assertFalse(receipt.budget_present)
        self.assertEqual(receipt.budget_proof_digest, body["budgetProofDigest"])
        changed = deepcopy(body)
        changed["owningVerificationDigest"] = "f" * 64
        with self.assertRaises(psycopg.Error):
            self._call(row, changed, as_role=True)
        row.refresh_from_db()
        self.assertEqual(row.status, "paused")
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessPromotionBudgetV10Attestation.objects.filter(pk=first).update(
                budget_proof_digest="0" * 64)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessFileRun.objects.filter(pk=row.pk).update(
                status="ready", error_code="", version=row.version + 1,
                progress_json='{"stage":"ready"}')

    def test_real_fixed_budget_presence_is_bound_without_ready(self):
        report = self._complete_budget_report()
        self.complete_flow(report)
        row = self._stage(report)
        body = self._body(row)
        self.assertTrue(body["budgetPresent"])
        self.assertEqual(body["budgetPlanDigest"],
            json.loads(report.snapshot_json)["budgetRef"]["planDigest"])
        wrong = deepcopy(body)
        wrong["budgetPresent"] = False
        with self.assertRaises(psycopg.Error):
            self._call(row, wrong, as_role=True)
        self._call(row, body, as_role=True)
        receipt = m.AiBusinessPromotionBudgetV10Attestation.objects.get(
            run_id=row.id, attempt=row.attempt)
        self.assertTrue(receipt.budget_present)
        self.assertEqual(receipt.budget_plan_digest, body["budgetPlanDigest"])
        row.refresh_from_db()
        self.assertEqual(row.status, "paused")

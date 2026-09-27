"""Isolated PostgreSQL sealed-v4 application HMAC and source replay checks."""
import hashlib
from unittest.mock import patch

from django.db import connection, transaction
from django.db import connection
from django.test import TransactionTestCase, override_settings

from access_control.models import AppUser
from business_analysis.contracts import canonical, digest
from finance.models import FinanceDataRevision
from netshop.models import NetshopDataRevision
from netshop.import_service import import_netshop_payload
from netshop.tests.factories import netshop_row, prepared_payload

from . import (business_v4_seal_hmac as seal_hmac,
    business_v4_seal_verify as verifier,
    business_v4_validation as validation, models as m)
from .policy import AiError
from .test_business_v4_seal_writer_gate import BusinessV4SealWriterGateTests as gate


class BusinessV4SealVerifyTests(TransactionTestCase):
    promotion_owner = gate.promotion_owner
    rebuild_plan = gate.rebuild_plan
    finance_owner = gate.finance_owner
    owner = gate.owner
    collect = gate.collect
    complete_mixed = gate.complete_mixed
    attempt = gate.attempt
    database = gate.database
    body = gate.body
    call_as_sealer = gate.call_as_sealer
    tearDown = gate.tearDown

    def setUp(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regprocedure('public.ai_v4_issue_seal_ticket("
                "text,text,text,bigint,bigint,text,text)')")
            if cursor.fetchone()[0] is not None:
                self.skipTest("0038直达封存验签夹具已由0041票据门禁关闭")
        gate.setUp(self)

    def sealed(self, *, authentic=True):
        attempt_id = self.attempt()
        body = self.body(attempt_id)
        signature = seal_hmac.sign(canonical(body))
        self.assertEqual(signature["keyId"], body["keyId"])
        self.call_as_sealer(body, mac=signature["bodyMac"] if authentic
            else "f" * 64)
        return body

    def test_real_application_mac_and_segments_verify_after_new_source_revision(self):
        body = self.sealed()
        before = (m.AiBusinessV4Chunk.objects.count(),
            m.AiBusinessV4ValidationSegment.objects.count())
        proof = verifier.verify_seal(self.parent.id, self.principal)
        self.assertEqual(proof["evidenceVersion"], body["evidenceVersion"])
        self.assertEqual(proof["sealedDigest"], hashlib.sha256(
            canonical(body).encode("utf-8")).hexdigest())
        self.assertTrue(proof["internalSealVerified"])
        self.assertTrue(proof["segmentHmacVerified"])
        self.assertFalse(proof["upstreamSignatureVerified"])
        self.assertFalse(proof["reportGenerationSupported"])
        self.assertEqual((m.AiBusinessV4Chunk.objects.count(),
            m.AiBusinessV4ValidationSegment.objects.count()), before)
        finance = FinanceDataRevision.objects.get(domain="finance")
        netshop = NetshopDataRevision.objects.get(domain="netshop")
        FinanceDataRevision.objects.filter(pk=finance.pk).update(
            revision=finance.revision + 1,
            source_digest=digest([finance.source_digest, "later-finance"]))
        NetshopDataRevision.objects.filter(pk=netshop.pk).update(
            revision=netshop.revision + 1,
            source_digest=digest([netshop.source_digest, "later-shop"]))
        self.assertEqual(verifier.verify_seal(self.parent.id,
            self.principal)["sealedDigest"], proof["sealedDigest"])

    def test_database_accepted_random_hex_mac_is_not_a_verified_seal(self):
        self.sealed(authentic=False)
        self.assertEqual(m.AiBusinessV4Run.objects.get(
            pk=self.parent.pk).status, "sealed")
        with self.assertRaises(AiError):
            verifier.verify_seal(self.parent.id, self.principal)

    def test_missing_segment_revoked_actor_and_unknown_key_are_rejected(self):
        self.sealed()
        original = m.AiBusinessV4ValidationSegment.objects.filter
        def omit(*args, **kwargs):
            return original(*args, **kwargs).exclude(
                source__source_key="finance-context")
        with patch.object(m.AiBusinessV4ValidationSegment.objects, "filter",
                side_effect=omit), self.assertRaises(AiError):
            verifier.verify_seal(self.parent.id, self.principal)
        with patch.object(validation, "_mac", return_value="0" * 64), \
                self.assertRaises(AiError):
            verifier.verify_seal(self.parent.id, self.principal)
        AppUser.objects.filter(email=self.principal.email).update(status="inactive")
        with self.assertRaises(AiError):
            verifier.verify_seal(self.parent.id, self.principal)
        AppUser.objects.filter(email=self.principal.email).update(status="active")
        with override_settings(DJANGO_INTERNAL_SECRET="rotated-seal-key-" + "x" * 48), \
                self.assertRaises(AiError):
            verifier.verify_seal(self.parent.id, self.principal)

    def test_unsealed_cross_run_and_reader_process_never_get_authority(self):
        other = m.AiBusinessV4Run.objects.exclude(pk=self.parent.pk).get(
            owner_email=self.principal.email)
        with self.assertRaises(AiError):
            verifier.verify_seal(self.parent.id, self.principal)
        self.sealed()
        with self.assertRaises(AiError):
            verifier.verify_seal(other.id, self.principal)
        with override_settings(DJANGO_PROCESS_ROLE="ai_reader"), \
                self.assertRaises(AiError):
            verifier.verify_seal(self.parent.id, self.principal)
        original = m.AiBusinessV4Seal.objects.filter
        with patch.object(m.AiBusinessV4Seal.objects, "filter",
                side_effect=lambda *args, **kwargs: original(*args, **kwargs).none()), \
                self.assertRaises(AiError):
            verifier.verify_seal(self.parent.id, self.principal)

    def test_restored_db_guards_do_not_hide_changed_raw_page_bytes(self):
        self.sealed()
        chunk = m.AiBusinessV4Chunk.objects.filter(run_id=self.parent.id).first()
        original_digest = chunk.payload_digest
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE public.ai_business_v4_chunks "
                    "DISABLE TRIGGER ai_immutable_v4")
                cursor.execute("ALTER TABLE public.ai_business_v4_chunks "
                    "DISABLE TRIGGER ai_v4_state")
                cursor.execute("UPDATE public.ai_business_v4_chunks "
                    "SET payload_json=payload_json||' ' WHERE id=%s", [chunk.id])
                cursor.execute("ALTER TABLE public.ai_business_v4_chunks "
                    "ENABLE TRIGGER ai_v4_state")
                cursor.execute("ALTER TABLE public.ai_business_v4_chunks "
                    "ENABLE TRIGGER ai_immutable_v4")
        changed = m.AiBusinessV4Chunk.objects.get(pk=chunk.pk)
        self.assertEqual(changed.payload_digest, original_digest)
        self.assertNotEqual(changed.payload_json, chunk.payload_json)
        with self.assertRaises(AiError):
            verifier.verify_seal(self.parent.id, self.principal)

    def test_legal_other_shop_import_during_unlocked_scan_becomes_historical(self):
        self.sealed()
        actual_scan = verifier._raw_scan
        calls = []
        def interleaved(source, cutoff, deadline):
            self.assertFalse(connection.in_atomic_block)
            if not calls:
                payload = prepared_payload(netshop_row(source="jd_promotion",
                    dataset="ad", shop_name="另一个独立店",
                    business_date="2026-08-21", sku_id="OTHER-SKU",
                    metrics={"spendCents": 100, "impressions": 20,
                        "clicks": 2}), raw_seed="verify-other-shop")
                outcome = import_netshop_payload(payload,
                    "verify-seal-synthetic@example.invalid")
                self.assertEqual(outcome["status"], "imported")
                calls.append(1)
            return actual_scan(source, cutoff, deadline)
        with patch.object(verifier, "_raw_scan", side_effect=interleaved):
            proof = verifier.verify_seal(self.parent.id, self.principal)
        self.assertEqual(calls, [1])
        self.assertTrue(proof["internalSealVerified"])
        by_domain = {"finance": None, "netshop": None}
        for item in proof["sourceRefs"]:
            source = m.AiBusinessV4Source.objects.get(run_id=self.parent.id,
                source_key=item["sourceKey"])
            by_domain[source.domain] = item["verificationFreshness"]
        self.assertEqual(by_domain["netshop"], "historical_revision")
        self.assertEqual(by_domain["finance"], "current_revision")

    def test_expired_full_scan_returns_resume_needed_not_partial_success(self):
        self.sealed()
        with patch.object(verifier, "MAX_VERIFY_SECONDS", 0), \
                self.assertRaises(AiError) as caught:
            verifier.verify_seal(self.parent.id, self.principal)
        self.assertEqual(caught.exception.code, "verification_requires_resume")

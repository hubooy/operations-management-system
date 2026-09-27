"""Actual persisted two-page v4 JD promotion full read-only replay."""
import json
from unittest.mock import patch

from django.test import TestCase
from access_control.models import AppUser

from . import business_v4_promotion_replay as replay
from . import test_business_v4_netshop_promotion as collector_fixture
from . import models as m
from .policy import AiError, canonical, digest


class BusinessV4PromotionReplayTests(TestCase):
    setUp = collector_fixture.BusinessV4NetshopPromotionTests.setUp
    owner = collector_fixture.BusinessV4NetshopPromotionTests.owner
    advance = collector_fixture.BusinessV4NetshopPromotionTests.advance
    three_window_run = collector_fixture.BusinessV4NetshopPromotionTests.three_window_run

    def complete(self):
        self.advance(1, "replay-first")
        self.advance(2, "replay-second")

    def inspect(self, **kwargs):
        return replay.inspect(self.parent.id, "promotion-current",
            self.principal, **kwargs)

    def test_full_replay_two_pages_internal_audit_and_no_seal_claim(self):
        self.complete()
        before = (m.AiBusinessV4Chunk.objects.count(),
            m.AiBusinessV4ToolReceipt.objects.count())
        result = self.inspect()
        self.assertEqual((result["pageCount"], result["rowCount"]), (2, 101))
        self.assertEqual(result["sourceKey"], "promotion-current")
        self.assertTrue(result["fullSourceReplayVerified"])
        self.assertTrue(result["internalToolAuditBound"])
        self.assertTrue(result["payloadCursorChainVerified"])
        self.assertTrue(result["requestCursorAuditVerified"])
        for field in ("upstreamSignatureVerified", "sealed",
                "persistentEvidenceVerified", "reportGenerationSupported",
                "registeredAgentTool", "registeredRenderer"):
            self.assertFalse(result[field])
        self.assertEqual(result["reconciliation"]["rowCount"], 101)
        self.assertEqual(result["proofDigest"], digest({k:v for k,v in result.items()
            if k != "proofDigest"}))
        self.assertEqual((m.AiBusinessV4Chunk.objects.count(),
            m.AiBusinessV4ToolReceipt.objects.count()), before)
        self.assertEqual(self.inspect(), result)

    def test_previous_and_year_ago_full_replay_are_separate_same_run_sources(self):
        self.three_window_run()
        version = 1
        expected = {"current": "2026-08-20", "previous": "2026-07-21",
            "yearAgo": "2025-08-20"}
        refs = set()
        for window in ("previous", "yearAgo", "current"):
            key = f"promotion-{window}"
            first = self.advance(version, f"replay-{window}-first", key)
            second = self.advance(first["runVersion"],
                f"replay-{window}-second", key)
            version = second["runVersion"]
            proof = replay.inspect(self.parent.id, key, self.principal)
            self.assertEqual((proof["pageCount"], proof["rowCount"]), (2, 101))
            self.assertEqual(proof["coverage"]["presentDates"], [expected[window]])
            self.assertTrue(proof["requestCursorAuditVerified"])
            self.assertFalse(proof["upstreamSignatureVerified"])
            self.assertNotIn(proof["sourceRef"], refs)
            refs.add(proof["sourceRef"])
        self.assertEqual(len(refs), 3)
        self.assertEqual(m.AiBusinessV4Run.objects.get(pk=self.parent.pk).status,
            "collecting")

    def test_previous_window_replay_rejects_current_window_request_audit(self):
        self.three_window_run()
        first = self.advance(1, "replay-previous-first", "promotion-previous")
        self.advance(first["runVersion"], "replay-previous-second",
            "promotion-previous")
        receipt = m.AiBusinessV4ToolReceipt.objects.get(run=self.parent,
            source=self.sources["promotion-previous"], sequence=1)
        current_arguments = {"domain": "netshop", **self.query, "limit": 100}
        class WrongWindowAudit:
            def __get__(self, instance, owner):
                if instance is None: return self
                return (canonical({"argumentsDigest": digest(current_arguments)})
                    if instance.id == receipt.audit_id else instance.__dict__.get("arguments_json"))
            def __set__(self, instance, value):
                instance.__dict__["arguments_json"] = value
        with patch.object(m.AiToolAuditLogs, "arguments_json", WrongWindowAudit()), \
                self.assertRaises(AiError):
            replay.inspect(self.parent.id, "promotion-previous", self.principal)

    def test_unfinished_foreign_source_and_wrong_baseline_not_authorized(self):
        with self.assertRaises(AiError):
            self.inspect()
        self.complete()
        for source_key in ("finance-context", "promotion-previous",
                "promotion-yearAgo"):
            with self.subTest(source_key=source_key), self.assertRaises(AiError):
                replay.inspect(self.parent.id, source_key, self.principal)

    def test_missing_middle_chunk_or_receipt_never_looks_complete(self):
        self.complete()
        original_chunks = m.AiBusinessV4Chunk.objects.filter
        def omit_chunk(*args, **kwargs):
            return original_chunks(*args, **kwargs).exclude(sequence=1)
        with patch.object(m.AiBusinessV4Chunk.objects, "filter", side_effect=omit_chunk), \
                self.assertRaises(AiError):
            self.inspect()
        original_receipts = m.AiBusinessV4ToolReceipt.objects.filter
        def omit_receipt(*args, **kwargs):
            return original_receipts(*args, **kwargs).exclude(sequence=1)
        with patch.object(m.AiBusinessV4ToolReceipt.objects, "filter",
                side_effect=omit_receipt), self.assertRaises(AiError):
            self.inspect()

    def test_wrong_coverage_and_revoked_actor_reject_after_full_scan(self):
        self.complete()
        original = replay._loaded
        def wrong_coverage(*args, **kwargs):
            actor, parent, source, query, saved, fp, fs = original(*args, **kwargs)
            changed = {**saved, "metadata": {**saved["metadata"],
                "coverage": {**saved["metadata"]["coverage"],
                    "presentDates": []}}}
            return actor, parent, source, query, changed, fp, fs
        # Exercise the independent final coverage reconciliation even when
        # page identity validation is bypassed inside this disposable probe.
        with patch.object(replay, "_loaded", side_effect=wrong_coverage), \
                patch.object(replay.daily_identity._Identity, "_identity"), \
                self.assertRaises(AiError):
            self.inspect()
        def revoke(event):
            if event == {"stage":"v4_promotion_replay","phase":"complete"}:
                AppUser.objects.filter(email=self.principal.email).update(status="inactive")
        with self.assertRaises(AiError):
            self.inspect(checkpoint=revoke)

    def test_receipt_chain_is_distinct_from_an_upstream_signature(self):
        self.complete()
        value = self.inspect()
        self.assertEqual(len(value["receiptChainDigest"]), 64)
        self.assertFalse(value["upstreamSignatureVerified"])
        self.assertTrue(value["requestCursorAuditVerified"])
        self.assertFalse(value["sealed"])

    def test_second_page_audit_digest_or_requested_cursor_mismatch_rejected(self):
        self.complete()
        first = json.loads(m.AiBusinessV4Chunk.objects.get(run=self.parent,
            source=self.sources["promotion-current"], sequence=1).payload_json)
        second = m.AiBusinessV4ToolReceipt.objects.get(run=self.parent,
            source=self.sources["promotion-current"], sequence=2)
        expected = {**self.query, "limit": 100,
            "cursor": first["pagination"]["nextCursor"],
            "expectedSourceRef": first["sourceRef"],
            "expectedRevision": first["sourceRevision"],
            "expectedLastId": int(first["items"][-1]["rowId"])}
        self.assertEqual(second.audit.arguments_json,
            canonical({"argumentsDigest": digest(expected)}))
        class TamperedAuditArguments:
            def __init__(self, target, replacement):
                self.target, self.replacement = target, replacement
            def __get__(self, instance, owner):
                if instance is None: return self
                value = instance.__dict__.get("arguments_json")
                return self.replacement if instance.id == self.target else value
            def __set__(self, instance, value):
                instance.__dict__["arguments_json"] = value
        for forged in (canonical({"argumentsDigest": "0" * 64}),
                       canonical({"argumentsDigest": digest({**expected,
                           "cursor": "different-signed-cursor"})})):
            with self.subTest(forged=forged), patch.object(m.AiToolAuditLogs,
                    "arguments_json", TamperedAuditArguments(second.audit_id, forged)), \
                    self.assertRaises(AiError):
                self.inspect()

    def test_checkpoint_byte_preflight_and_extra_metric_are_rejected_before_reconcile(self):
        with patch.object(replay.json, "loads", side_effect=AssertionError("must not parse")) as parser:
            with self.assertRaises(AiError):
                replay._checkpoint("x"*(replay.collector.MAX_CHECKPOINT_BYTES+1))
            parser.assert_not_called()
        self.complete()
        chunk = m.AiBusinessV4Chunk.objects.get(run=self.parent,
            source=self.sources["promotion-current"], sequence=1)
        page = json.loads(chunk.payload_json)
        page["items"][0]["metrics"]["injectedMetric"] = 1
        with self.assertRaises(AiError):
            replay._promotion_page(page, first=True)
        page["items"][0]["metrics"].pop("injectedMetric")
        page["control"]["typedTotals"]["injectedMetric"] = 0
        with self.assertRaises(AiError):
            replay._promotion_page(page, first=True)

    def test_time_and_proof_byte_limits_fail_closed(self):
        self.complete()
        with patch.object(replay, "MAX_REPLAY_SECONDS", 0), self.assertRaises(AiError):
            self.inspect()
        with patch.object(replay, "MAX_PROOF_BYTES", 1), self.assertRaises(AiError):
            self.inspect()

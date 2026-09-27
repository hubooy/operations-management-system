"""Unbound report bridge checks and isolated real sealed-v4 ORM target."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from business_analysis import period_bound_plan_v1
from business_analysis.test_report_v4_source_bridge_candidate import (
    fixture as bridge_fixture)
from business_analysis.test_report_v4_promotion_stream_candidate import (
    fixture as stream_fixture, Sink)

from . import business_v4_report_stream_owning_candidate as owning
from .policy import AiError, canonical, digest


class UnboundV4ReportOwnerPureTests(SimpleTestCase):
    def test_test_only_gate_refuses_default_and_non_test_writer(self):
        with self.assertRaises(AiError):
            owning._test_only(False)
        with override_settings(DJANGO_ENVIRONMENT="production",
                DJANGO_PROCESS_ROLE="ai_writer"), self.assertRaises(AiError):
            owning._test_only(True)
        with override_settings(DJANGO_ENVIRONMENT="test",
                DJANGO_PROCESS_ROLE="ai_reader"), self.assertRaises(AiError):
            owning._test_only(True)

    def contexts(self):
        intent, plan, seal = bridge_fixture()
        period = period_bound_plan_v1.prepare_candidate(plan)
        sources = [SimpleNamespace(id=f"source-{index}",
            source_key=entry["sourceKey"],
            query_digest=entry["queryDigest"],
            source_identity_digest=entry["sourceIdentityDigest"],
            source_ref=seal["sourceRefs"][index]["sourceRef"],
            source_revision=seal["sourceRefs"][index]["sourceRevision"])
            for index, entry in enumerate(plan["sourcePlans"])]
        report = SimpleNamespace(id=intent["reportId"],
            owner_email=intent["ownerEmail"], scope_json="null",
            snapshot_json="{}", workflow=SimpleNamespace(input_json="{}"))
        evidence = SimpleNamespace(id=intent["v2EvidenceRunId"],
            version=intent["v2EvidenceVersion"])
        snapshot = {"sealedDigest": intent["v2SealedDigest"]}
        scope = {"platform": "京东", "shop": intent["shop"],
            **intent["originalPeriod"]}
        fixed = {"workflowInputDigest": digest("{}")}
        parent = SimpleNamespace(id=intent["v4RunId"],
            owner_email=intent["ownerEmail"], scope_json="null",
            client_request_id=plan["clientRequestId"],
            version=seal["evidenceVersion"])
        left = report, snapshot, evidence, fixed, plan["analysisRequest"], scope
        right = ({"email": intent["ownerEmail"]}, parent, sources,
            "d" * 64, seal, {}, plan, period, {})
        return left, right

    def test_three_window_unbound_claim_never_grants_read_or_file(self):
        report, v4 = self.contexts()
        value = owning._unbound(report, v4)
        self.assertEqual(value["schemaVersion"], owning.SCHEMA)
        self.assertEqual([row["window"] for row in value[
            "promotionWindows"]], ["current", "previous", "yearAgo"])
        self.assertEqual(value["v2SealedDigest"], report[1]["sealedDigest"])
        self.assertEqual(value["v4SealedDigest"], v4[4]["sealedDigest"])
        self.assertFalse(value["persistedSameReportLinkVerified"])
        self.assertFalse(value["v4RowsReadableForReport"])
        self.assertFalse(value["publishable"])
        self.assertEqual(value["candidateDigest"], digest({key: item
            for key, item in value.items() if key != "candidateDigest"}))

    def test_cross_shop_period_identity_or_intent_refuses(self):
        report, v4 = self.contexts()
        for label, mutate in (
                ("shop", lambda left, right: left[5].update(shop="另一店")),
                ("period", lambda left, right: left[5].update(endDate="2026-09-13")),
                ("owner", lambda left, right: setattr(right[1], "owner_email",
                    "other@example.test"),
                ),
                ("source_revision", lambda left, right: setattr(right[2][0],
                    "source_revision", "8:" + "a" * 64)),
                ("question", lambda left, right: left[4].update(question="different"))):
            changed_left, changed_right = deepcopy(report), deepcopy(v4)
            mutate(changed_left, changed_right)
            with self.subTest(label=label), self.assertRaises(AiError):
                owning._unbound(changed_left, changed_right)

    def test_source_manifest_binds_seal_receipt_and_checkpoint(self):
        report, v4 = self.contexts()
        bridge = owning._unbound(report, v4)
        source = next(row for row in v4[2] if row.source_key ==
            "promotion-current")
        source.page_count = 2
        source.row_count = 101
        source.stored_bytes = 2048
        source.checkpoint_json = canonical({"verifier": {
            "evidence_digest": "c" * 64}})
        seal_item = {"sourceKey": source.source_key,
            "queryDigest": source.query_digest,
            "sourceRef": source.source_ref,
            "sourceRevision": source.source_revision,
            "pageCount": source.page_count,
            "rowCount": source.row_count,
            "storedBytes": source.stored_bytes,
            "receiptChainDigest": "b" * 64}
        value = owning._source_manifest(bridge, source, seal_item)
        self.assertEqual(value["receiptChainDigest"], "b" * 64)
        self.assertEqual(value["evidenceDigest"], "c" * 64)
        self.assertEqual(value["manifestDigest"], digest({key: item
            for key, item in value.items() if key != "manifestDigest"}))
        bad = {**seal_item, "sourceRevision": "8:" + "a" * 64}
        with self.assertRaises(AiError):
            owning._source_manifest(bridge, source, bad)

    @override_settings(DJANGO_PROCESS_ROLE="development",
        DJANGO_ENVIRONMENT="test")
    def test_test_only_adapter_stages_privately_and_returns_blocked_unbound(self):
        left, right = self.contexts()
        bridge, manifest, page = stream_fixture()
        actor, parent, sources, directory, verified, body, plan, period, _ = right
        body = {"sources": [{"sourceKey": "promotion-current"}]}
        for index, source in enumerate(sources):
            source.version = index + 2
            source.checkpoint_json = "{}"
        current = next(row for row in sources if row.source_key ==
            "promotion-current")
        seal_state = {"body_digest": verified["sealedDigest"],
            "body_json": "{}", "body_mac": "a" * 64,
            "key_id": "b" * 16}
        right = (actor, parent, sources, directory, verified, body, plan,
            period, seal_state)
        queryset = Mock()
        queryset.values.return_value.first.return_value = seal_state
        sink = Sink()
        with patch.object(owning, "_report", return_value=left), \
                patch.object(owning, "_v4", return_value=right), \
                patch.object(owning, "_unbound", return_value=bridge), \
                patch.object(owning, "_source_manifest", return_value=manifest), \
                patch.object(owning, "_pages", side_effect=lambda *_:
                    iter([deepcopy(page)])) as pages, \
                patch.object(owning.v4_owner, "_directory",
                    return_value=(actor, parent, sources, directory)), \
                patch.object(owning.v4_owner, "verify_seal",
                    return_value=verified), \
                patch.object(owning.m.AiBusinessV4Seal.objects, "filter",
                    return_value=queryset):
            result = owning.stage_unbound(left[0].id, parent.id, "current",
                SimpleNamespace(email=actor["email"], scope=None), sink,
                enabled=True)
        self.assertEqual(pages.call_count, 2)
        self.assertEqual(len(sink.rows), 30)
        self.assertEqual(result["status"], "blocked_unbound")
        self.assertFalse(result["persistedSameReportLinkVerified"])
        self.assertFalse(result["sameReportCompositionSupported"])
        self.assertFalse(result["fileDownloadSupported"])

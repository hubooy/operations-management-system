"""Source-iterator bridge checks; isolated PG target uses a real sealed run."""
from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from business_analysis.test_cross_source_daily_columns import CONTEXT
from business_analysis.test_cross_source_category_spu_compare import (
    complete_fixture)
from business_analysis.test_report_composition_volume_stream_v1 import Sink

from . import business_report_composition_promotion_stream as owning
from . import business_report_composition_owning as report_owner
from . import test_business_cross_source_daily_materials as daily_fixture
from .policy import AiError, digest


class PromotionStreamBridgePureTests(TestCase):
    def fixture(self):
        plan, sources, infos, keys, _, _, native = complete_fixture()
        fixed = {"reportId": plan["reportId"], "synthetic": "sealed-v2"}
        base_body = {"schemaVersion": report_owner.SCHEMA,
            "reportBinding": fixed, "planDigest": plan["planDigest"],
            "sourceKeys": keys,
            "sourceEvidenceDigests": {key:
                infos[key]["expected"]["evidenceDigest"] for key in infos},
            "sourceRevisions": {key:
                infos[key]["metadata"]["sourceRevision"] for key in infos},
            "promotionSourceKeys": [keys["promotion"]["current"]],
            "pairedBytesVerified": True, "tableCount": 13,
            "published": False, "agentReadPersisted": False}
        base = {**base_body, "resultDigest": digest(base_body)}
        snapshot = {"sealedDigest": CONTEXT["sealedDigest"],
            "mappingPlan": plan["mappingPlan"],
            "mappingPlanDigest": plan["mappingPlanDigest"]}
        evidence = SimpleNamespace(id=CONTEXT["evidenceRunId"],
            version=CONTEXT["evidenceVersion"])
        class Reader:
            calls = []
            def __init__(self, *_):
                pass
            def pages(self, key, checkpoint=None):
                assert key == keys["promotion"]["current"]
                self.calls.append(key)
                return iter(deepcopy(native["promotion"]))
        return base, keys["promotion"]["current"], (
            snapshot, evidence, sources, fixed, infos), Reader

    def test_same_report_authoritative_page_iterator_to_private_volumes(self):
        base, key, bound, reader = self.fixture()
        sink = Sink()
        with patch.object(report_owner, "_snapshot", return_value=bound), \
                patch.object(owning, "Reader", reader):
            value = owning.prepare(base["reportBinding"]["reportId"],
                base, key, SimpleNamespace(email=CONTEXT["ownerEmail"],
                    scope=CONTEXT["scope"]), sink, enabled=True)
        self.assertFalse(sink.aborted)
        self.assertEqual(value["sourceManifest"]["rowCount"], 2)
        self.assertEqual(value["volumeManifest"]["sourceRowCount"], 2)
        self.assertEqual(value["volumeManifest"]["volumeCount"], 1)
        self.assertEqual(value["owningReceipt"][
            "completeSealedV2PagesReplayedTwice"], True)
        self.assertEqual(reader.calls, [key, key])
        self.assertFalse(value["owningReceipt"]["sourceCapacityExtendedBeyondV2"])
        self.assertEqual(sink.final, value["volumeManifest"])

    def test_closed_wrong_report_and_revision_drift_refuse(self):
        base, key, bound, reader = self.fixture()
        sink = Sink()
        principal = SimpleNamespace(email=CONTEXT["ownerEmail"],
            scope=CONTEXT["scope"])
        with patch.object(report_owner, "_snapshot", return_value=bound), \
                patch.object(owning, "Reader", reader):
            with self.assertRaises(AiError):
                owning.prepare(base["reportBinding"]["reportId"],
                    base, key, principal, sink)
            wrong = deepcopy(base)
            wrong["reportBinding"]["reportId"] = "other"
            wrong["resultDigest"] = digest({name: value for name, value
                in wrong.items() if name != "resultDigest"})
            with self.assertRaises(AiError):
                owning.prepare("other", wrong, key, principal, sink,
                    enabled=True)
        changed = deepcopy(bound)
        changed[4][key]["metadata"]["sourceRevision"] = "changed"
        calls = 0
        def drift(*_):
            nonlocal calls
            calls += 1
            return bound if calls == 1 else changed
        with patch.object(report_owner, "_snapshot", side_effect=drift), \
                patch.object(owning, "Reader", reader), \
                self.assertRaises(AiError):
            owning.prepare(base["reportBinding"]["reportId"],
                base, key, principal, Sink(), enabled=True)


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",
    DJANGO_ENVIRONMENT="test")
class PromotionStreamBridgeRoleTests(djtest.TransactionTestCase):
    user = daily_fixture.BusinessCrossSourceDailyMaterialTests.user
    call = daily_fixture.BusinessCrossSourceDailyMaterialTests.call
    bundle = daily_fixture.BusinessCrossSourceDailyMaterialTests.bundle
    input_for = daily_fixture.BusinessCrossSourceDailyMaterialTests.input_for
    insert = daily_fixture.BusinessCrossSourceDailyMaterialTests.insert
    seed = daily_fixture.BusinessCrossSourceDailyMaterialTests.seed
    collect_body = daily_fixture.BusinessCrossSourceDailyMaterialTests.collect_body
    _native = daily_fixture.BusinessCrossSourceDailyMaterialTests._native
    _recollect = daily_fixture.BusinessCrossSourceDailyMaterialTests._recollect

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires isolated sealed-v2 PostgreSQL guards")
        daily_fixture.BusinessCrossSourceDailyMaterialTests.setUp(self)

    def test_real_sealed_reader_two_full_passes_and_no_fact_tables(self):
        base = report_owner.prepare(self.report.id, self.keys, self.admin,
            enabled=True)["manifest"]
        sink = Sink()
        with CaptureQueriesContext(connection) as queries:
            value = owning.prepare(self.report.id, base, "ads", self.admin,
                sink, enabled=True)
        self.assertGreaterEqual(value["sourceManifest"]["rowCount"], 1)
        self.assertEqual(value["volumeManifest"]["sourceRowCount"],
            value["sourceManifest"]["rowCount"])
        self.assertFalse(value["owningReceipt"]["agentReadPersisted"])
        self.assertEqual(sink.final, value["volumeManifest"])
        self.assertFalse(any(table in query["sql"].lower() for query in
            queries for table in ("netshop_rows", "sales_order_lines")))

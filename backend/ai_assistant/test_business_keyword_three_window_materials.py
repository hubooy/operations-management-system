"""Pure contract tests for the closed three-window keyword owning adapter."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from django import test as djtest

from . import business_keyword_three_window_materials as material
from . import test_business_cross_source_daily_materials as daily_fixture
from .policy import AiError


def row(keyword, sku, amount, *, baseline=None, comparisons=None):
    return {"rowIndex": 0, "entity": {"platform": "京东",
        "shopName": "店A", "keyword": keyword, "promotedSkuId": sku},
        "currentRowCount": 1 if amount is not None else None,
        "baselineRowCount": 1 if baseline is not None else None,
        "metrics": {"spendCents": {"value": amount,
            "missingRows": 0}} if amount is not None else None,
        "baselineMetrics": {"spendCents": {"value": baseline,
            "missingRows": 0}} if baseline is not None else None,
        "ratios": {}, "comparisons": comparisons or {},
        "identityQualified": keyword is not None and sku is not None,
        "missingIdentityFields": [key for key, value in (
            ("keyword", keyword), ("promotedSkuId", sku)) if value is None]}


def header(window=None):
    return {"sourceMetadata": {"metricSemantics": "same"},
        "sourceQueryDigest": "q", "periods": {"current": "C",
            "previous": "P", "yearAgo": "Y"},
        "sourceWindow": "current", "comparisonWindow": window,
        "authorityVerified": False, "source": {"key": "ads-current"},
        "baselineSource": {"key": "ads-" + window} if window else None,
        "total": 1}


class KeywordThreeWindowPureTests(TestCase):
    def test_owned_scan_must_finish_exact_declared_row_count(self):
        fixed = {"reportId": "report-1"}
        actor = SimpleNamespace(email="owner@example.invalid", scope=None)
        current = row("切肉机", "S1", 100)
        opened = SimpleNamespace(header=lambda: header(),
            scan=lambda: iter([current]))
        binding = {"reportBinding": fixed, "sourceKey": "ads-current",
            "baselineKey": None, "view": "keyword_sku"}
        @contextmanager
        def owned(*_args, **_kwargs):
            yield opened, binding
        with patch.object(material.keyword_owner, "table", side_effect=owned):
            actual, rows, proof = material._read("report-1",
                "ads-current", None, actor, fixed, None)
            self.assertEqual(actual["total"], len(rows))
            self.assertEqual(len(proof), 64)
            opened.header = lambda: {**header(), "total": 2}
            with self.assertRaises(AiError):
                material._read("report-1", "ads-current", None,
                    actor, fixed, None)

    def test_combines_two_baselines_without_conflating_missing_identity(self):
        current = row(None, None, 100)
        previous = row(None, None, 100, baseline=80,
            comparisons={"spendCents": {"status": "unavailable",
                "difference": None}})
        year_ago = row(None, None, 100, baseline=60,
            comparisons={"spendCents": {"status": "unavailable",
                "difference": None}})
        identity = material.canonical(current["entity"])
        rows = material._combine(header(), {identity: current}, {
            "previous": (header("previous"), {identity: previous}, "p"),
            "yearAgo": (header("yearAgo"), {identity: year_ago}, "y")},
            {"current": "ads-current", "previous": "ads-previous",
                "yearAgo": "ads-yearAgo"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["windows"]["previous"]["metrics"]
            ["spendCents"]["value"], 80)
        self.assertEqual(rows[0]["windows"]["yearAgo"]["metrics"]
            ["spendCents"]["value"], 60)
        self.assertFalse(rows[0]["identityQualified"])
        self.assertIsNone(rows[0]["comparisons"]["previous"]
            ["spendCents"]["difference"])

    def test_absent_baseline_and_entity_remain_distinct(self):
        current = row("切肉机", "S1", 100)
        identity = material.canonical(current["entity"])
        rows = material._combine(header(), {identity: current}, {},
            {"current": "ads-current", "previous": None,
                "yearAgo": None})
        self.assertEqual(rows[0]["windows"]["previous"]["status"],
            "missing_source")
        self.assertIsNone(rows[0]["comparisons"]["yearAgo"])

    def test_changed_current_or_semantics_refuses_merge(self):
        current = row("切肉机", "S1", 100)
        identity = material.canonical(current["entity"])
        changed = row("切肉机", "S1", 101)
        with self.assertRaises(AiError):
            material._combine(header(), {identity: current}, {
                "previous": (header("previous"), {identity: changed}, "p")},
                {"current": "ads-current", "previous": "ads-previous",
                    "yearAgo": None})
        changed_header = header("previous")
        changed_header["sourceMetadata"] = {"metricSemantics": "changed"}
        with self.assertRaises(AiError):
            material._combine(header(), {identity: current}, {
                "previous": (changed_header, {identity: current}, "p")},
                {"current": "ads-current", "previous": "ads-previous",
                    "yearAgo": None})

    def test_closed_prepare_and_final_report_revalidation(self):
        actor = SimpleNamespace(email="owner@example.invalid", scope=None)
        keys = {"promotion": {"current": "ads-current",
            "previous": None, "yearAgo": None}}
        with patch.object(material.report_binding, "_load") as load:
            with self.assertRaises(AiError):
                material.prepare("report-1", keys, actor)
            load.assert_not_called()
        fixed = {"reportId": "report-1", "evidenceRunId": "evidence-1",
            "evidenceVersion": 1, "sealedDigest": "a"*64,
            "mappingPlanDigest": "b"*64}
        info = {"ads-current": {"expected": {"evidenceDigest": "c"*64},
            "metadata": {"sourceRevision": "revision-1"}}}
        plan = {"mappingPlan": {}, "mappingPlanDigest": "b"*64,
            "periods": header()["periods"], "planDigest": "d"*64}
        current = row("切肉机", "S1", 100)
        identity = material.canonical(current["entity"])
        with patch.object(material.daily_owner, "_selection", return_value=keys), \
                patch.object(material.report_binding, "_load", return_value=(
                    fixed, None, None, {}, [], info)), \
                patch.object(material.planning, "prepare_candidate", return_value=plan), \
                patch.object(material, "_read", return_value=(header(),
                    {identity: current}, "t")), \
                patch.object(material.report_binding, "_revalidate") as fresh:
            value = material.prepare("report-1", keys, actor, enabled=True)
            fresh.assert_called_once_with(fixed, actor)
        self.assertEqual(value["rowCount"], 1)
        self.assertFalse(value["authorityVerified"])
        self.assertFalse(value["published"])
        self.assertIsNone(value["tableDigests"]["previous"])
        self.assertEqual(value["resultDigest"], material.digest({key: cell
            for key, cell in value.items() if key != "resultDigest"}))
        with patch.object(material.daily_owner, "_selection", return_value=keys), \
                patch.object(material.report_binding, "_load", return_value=(
                    fixed, None, None, {}, [], info)), \
                patch.object(material.planning, "prepare_candidate", return_value=plan), \
                patch.object(material, "_read", return_value=(header(),
                    {identity: current}, "t")), \
                patch.object(material.report_binding, "_revalidate",
                    side_effect=AiError("revoked", "conflict", 409)):
            with self.assertRaisesRegex(AiError, "revoked"):
                material.prepare("report-1", keys, actor, enabled=True)


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",
    DJANGO_ENVIRONMENT="test")
class KeywordThreeWindowOwningTests(djtest.TransactionTestCase):
    """Real sealed-v2 page target; the project runs it in isolated PostgreSQL."""
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
        from django.db import connection
        if connection.vendor != "postgresql":
            self.skipTest("requires isolated sealed-v2 PostgreSQL guards")
        daily_fixture.BusinessCrossSourceDailyMaterialTests.setUp(self)

    def test_real_current_source_and_unknown_baselines(self):
        with patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote:
            value = material.prepare(self.report.id, self.keys, self.admin,
                enabled=True)
        model.assert_not_called(); remote.assert_not_called()
        self.assertEqual(value["sourceKeys"]["previous"], None)
        self.assertEqual(value["sourceKeys"]["yearAgo"], None)
        self.assertTrue(value["completeSealedV2SourcesReplayed"])
        self.assertFalse(value["agentReadPersisted"])
        self.assertFalse(value["registeredRenderer"])
        self.assertFalse(value["authorityVerified"])
        self.assertEqual(value["rowCount"], len(value["rows"]))
        for row in value["rows"]:
            self.assertEqual(row["windows"]["previous"]["status"],
                "missing_source")
            self.assertEqual(row["windows"]["yearAgo"]["status"],
                "missing_source")

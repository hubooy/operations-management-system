"""Isolated PostgreSQL target for the closed sealed-v2 paired preview."""
from unittest import TestCase
from unittest.mock import patch
from types import SimpleNamespace
from contextlib import contextmanager
import io
import json
import zipfile

from django import test as djtest
from django.db import connection
from access_control.models import AppUser

from . import business_report_composition_owning as owner
from . import test_business_cross_source_daily_materials as daily_fixture
from .policy import AiError


class BusinessReportCompositionPairPureTests(TestCase):
    def test_category_spu_replays_extra_erp_grains_and_native_source(self):
        from business_analysis import cross_source_category_spu_compare as pure
        from business_analysis.test_cross_source_category_spu_compare import (
            complete_fixture)
        from business_analysis.test_cross_source_daily_columns import CONTEXT
        plan, sources, infos, keys, manifest, five, native = complete_fixture()
        @contextmanager
        def erp(*_args, **_kwargs):
            yield SimpleNamespace(manifest=manifest,
                ndjson_pages=lambda kind: iter(five[kind]))
        class NativeReader:
            def __init__(self, *_args):
                pass
            def pages(self, key, checkpoint=None):
                self_key = keys["netshopSpu"]["current"]
                assert key == self_key
                return iter(native["netshopSpu"])
        with patch.object(owner.erp_owner, "_bound", return_value=(
                None, None, SimpleNamespace(), sources, None)), \
                patch.object(owner.erp_owner, "prepare", side_effect=erp), \
                patch.object(owner, "Reader", NativeReader):
            result = owner._category_result("report-1", keys,
                SimpleNamespace(), plan, sources, infos, CONTEXT, None, None)
        expected = pure.prepare_candidate(plan, sources, infos, CONTEXT, keys,
            {"current": (manifest, five), "previous": None, "yearAgo": None},
            {"current": native["netshopSpu"], "previous": None,
                "yearAgo": None})
        self.assertEqual(result["comparisonDigest"], expected["comparisonDigest"])

    def _synthetic_owned(self, *, drift=False):
        from business_analysis import cross_source_sku_window_compare as compared
        from business_analysis.test_report_composition_v1 import fixtures
        from business_analysis.test_cross_source_daily_columns import CONTEXT
        case = fixtures()
        plan, sources, infos, keys, materials, category = case
        fixed = {"reportId": plan["reportId"], "synthetic": "fixed-seal"}
        snapshot = {"sealedDigest": CONTEXT["sealedDigest"],
            "mappingPlan": plan["mappingPlan"],
            "mappingPlanDigest": plan["mappingPlanDigest"]}
        evidence = SimpleNamespace(id=CONTEXT["evidenceRunId"],
            version=CONTEXT["evidenceVersion"])
        state = {"count": 0}
        def current(*_):
            state["count"] += 1
            if drift and state["count"] > 1:
                return snapshot, evidence, sources, fixed, {**infos,
                    keys["master"]: {**infos[keys["master"]],
                        "metadata": {**infos[keys["master"]]["metadata"],
                            "sourceRevision": "changed"}}}
            return snapshot, evidence, sources, fixed, infos
        comparison = compared.prepare_candidate(plan, sources, infos,
            CONTEXT, keys, materials)
        sku_owned = {"binding": {"reportBinding": fixed,
            "planDigest": plan["planDigest"],
            "materialDigests": {window: material["materialDigest"]
                for window, material in materials.items()}},
            "comparison": comparison}
        def daily(*args, **_):
            window = args[3]
            result = {"reportBinding": fixed,
                "planDigest": plan["planDigest"],
                "material": materials[window]}
            return {**result, "resultDigest": owner.digest(result)}
        return keys, current, sku_owned, category, daily

    def test_owned_synthetic_full_pair_and_final_revision_drift(self):
        keys, current, sku_owned, category, daily = self._synthetic_owned()
        with patch.object(owner, "_snapshot", side_effect=current), \
                patch.object(owner.sku_owner, "prepare", return_value=sku_owned), \
                patch.object(owner.daily_owner, "prepare", side_effect=daily), \
                patch.object(owner, "_category_result", return_value=category):
            value = owner.prepare("synthetic-report", keys,
                SimpleNamespace(email="owner@example.invalid", scope=None),
                enabled=True)
        self.assertEqual(value["manifest"]["tableCount"], 13)
        self.assertEqual(len(value["tables"]), 13)
        self.assertEqual(len(value["pairReceipt"]["tables"]), 13)
        self.assertTrue(value["manifest"]["pairedBytesVerified"])
        self.assertFalse(value["manifest"]["published"])
        keys, current, sku_owned, category, daily = self._synthetic_owned()
        with patch.object(owner, "_snapshot", side_effect=current), \
                patch.object(owner.sku_owner, "prepare", return_value=sku_owned), \
                patch.object(owner.daily_owner, "prepare", side_effect=daily), \
                patch.object(owner, "_category_result") as category_call:
            without_category = owner.prepare("synthetic-report", keys,
                SimpleNamespace(email="owner@example.invalid", scope=None),
                enabled=True, include_category_spu=False)
        category_call.assert_not_called()
        self.assertFalse(without_category["manifest"]["categorySpuPresent"])
        self.assertEqual([entry["rowCount"] for entry in
            without_category["manifest"]["tableAudit"][3:6]], [0, 0, 0])
        keys, current, sku_owned, category, daily = self._synthetic_owned(
            drift=True)
        with patch.object(owner, "_snapshot", side_effect=current), \
                patch.object(owner.sku_owner, "prepare", return_value=sku_owned), \
                patch.object(owner.daily_owner, "prepare", side_effect=daily), \
                patch.object(owner, "_category_result", return_value=category), \
                self.assertRaises(AiError):
            owner.prepare("synthetic-report", keys,
                SimpleNamespace(email="owner@example.invalid", scope=None),
                enabled=True)

    def test_synthetic_same_tables_have_thirteen_matching_proofs(self):
        from business_analysis.test_report_composition_tables_v1 import (
            inputs, fixtures)
        args, options = inputs(fixtures())
        metadata, tables = owner.table_projection.prepare(*args, **options)
        xlsx, html = io.BytesIO(), io.BytesIO()
        receipt = owner.write_pair(xlsx, html, title="合成组合预览",
            metadata=metadata, tables=tables, xlsx_opc_version=2)
        owner._verify_pair(metadata, tables, receipt,
            xlsx.getvalue(), html.getvalue())
        self.assertEqual([table.key for table in tables],
            list(owner.table_projection.TABLE_KEYS))

    def test_changed_html_or_receipt_is_rejected(self):
        from business_analysis.test_report_composition_tables_v1 import (
            inputs, fixtures)
        args, options = inputs(fixtures())
        metadata, tables = owner.table_projection.prepare(*args, **options)
        xlsx, html = io.BytesIO(), io.BytesIO()
        receipt = owner.write_pair(xlsx, html, title="合成组合预览",
            metadata=metadata, tables=tables, xlsx_opc_version=2)
        altered_html = html.getvalue().replace(b'"key":"store_daily"',
            b'"key":"changed_daily"', 1)
        with self.assertRaises(AiError):
            owner._verify_pair(metadata, tables, receipt,
                xlsx.getvalue(), altered_html)
        altered_receipt = {**receipt, "tables":
            [{**receipt["tables"][0], "rowDigest": "0"*64},
                *receipt["tables"][1:]]}
        with self.assertRaises(AiError):
            owner._verify_pair(metadata, tables, altered_receipt,
                xlsx.getvalue(), html.getvalue())


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",
    DJANGO_ENVIRONMENT="test")
class BusinessReportCompositionOwningTests(djtest.TransactionTestCase):
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

    def prepared(self, **kwargs):
        return owner.prepare(self.report.id, self.keys, self.admin,
            enabled=True, **kwargs)

    def test_same_sealed_report_thirteen_tables_and_paired_bytes(self):
        report_before = self.report.snapshot_json
        workflow_before = self.report.workflow.input_json
        with patch("ai_assistant.transport.execute_tool") as remote:
            value = self.prepared()
        remote.assert_not_called()
        manifest = value["manifest"]
        self.assertEqual(manifest["tableCount"], 13)
        self.assertEqual(len(value["tables"]), 13)
        self.assertEqual(len(manifest["tableAudit"]), 12)
        self.assertTrue(manifest["categorySpuPresent"])
        self.assertTrue(manifest["pairedBytesVerified"])
        self.assertTrue(manifest["financeB2bMarketContextOnly"])
        self.assertFalse(manifest["published"])
        self.assertFalse(manifest["agentReadPersisted"])
        self.assertFalse(manifest["authorityVerified"])
        self.assertIn(b"<html", value["html"])
        with zipfile.ZipFile(io.BytesIO(value["xlsx"])) as workbook:
            receipt = json.loads(workbook.read("teruisi-manifest.json"))
        self.assertEqual(len(receipt["tables"]), 13)
        self.assertEqual([entry["key"] for entry in receipt["tables"]],
            list(owner.table_projection.TABLE_KEYS))
        self.assertEqual([entry["rowDigest"] for entry in receipt["tables"][:12]],
            [entry["rowDigest"] for entry in manifest["tableAudit"]])
        self.report.refresh_from_db()
        self.report.workflow.refresh_from_db()
        self.assertEqual(self.report.snapshot_json, report_before)
        self.assertEqual(self.report.workflow.input_json, workflow_before)

    def test_default_closed_wrong_actor_and_final_admin_revocation(self):
        with patch.object(owner.sku_owner, "prepare",
                side_effect=AssertionError("must remain closed")) as work:
            with self.assertRaises(AiError):
                owner.prepare(self.report.id, self.keys, self.admin)
            work.assert_not_called()
        for report_id, principal in (("missing-report", self.admin),
                (self.report.id, self.viewer),
                (self.report.id, self.user("composition-other@example.invalid",
                    "admin", None))):
            with self.subTest(report_id=report_id), self.assertRaises(AiError):
                owner.prepare(report_id, self.keys, principal, enabled=True)
        def revoke(event):
            if event == {"stage": "report_composition_owning",
                    "phase": "complete"}:
                AppUser.objects.filter(email=self.admin.email).update(
                    status="disabled")
        with self.assertRaises(AiError):
            self.prepared(checkpoint=revoke)

    def test_final_source_revision_drift_and_writer_receipt_tamper_refuse(self):
        original = owner._snapshot
        calls = 0
        def drift(*args):
            nonlocal calls
            calls += 1
            parts = original(*args)
            if calls > 1:
                infos = {key: {**info, "metadata":
                    {**info["metadata"], "sourceRevision": "changed"}}
                    for key, info in parts[4].items()}
                return (*parts[:4], infos)
            return parts
        with patch.object(owner, "_snapshot", side_effect=drift), \
                self.assertRaises(AiError):
            self.prepared()
        self.assertGreater(calls, 1)
        with patch.object(owner, "_verify_pair",
                side_effect=AiError("bad writer proof", "conflict", 409)):
            with self.assertRaises(AiError):
                self.prepared(include_category_spu=False)

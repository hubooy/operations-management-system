"""Temporary multi-volume export using synthetic sealed-reader projections."""
from copy import deepcopy
from io import BytesIO
import hashlib
import json
import time
import zipfile
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from zipfile import ZipFile

from business_analysis.contracts import PageReconciler
from business_analysis.test_results import fixture
from business_analysis.volume_files import VolumeStreams
from business_analysis.report_files import Column, Table
from . import business_export as export
from .policy import AiError, canonical, digest


class BusinessExportV2Tests(TestCase):
    def setUp(self):
        self.principal = SimpleNamespace(email="synthetic@example.invalid", scope=None)
        self.sources, self.pages, self.infos, self.reads, self.states = [], {}, {}, [], {}
        self.value = {"sections": [{"title": "合成诊断", "body": "仅用于独立文件验收；不代表真实模型分析。"}],
            "diagnosis": {"findings": [{"id": "f1", "kind": "gap", "title": "合成缺口", "explanation": "等待业务复核", "facts": []}]}}
        owner = self
        class SyntheticReader:
            def __init__(self, evidence, principal):
                owner.assertIs(evidence, owner.evidence)
                owner.assertIs(principal, owner.principal)
            @property
            def sources(self):
                return deepcopy(owner.sources)
            def info(self, key):
                return deepcopy(owner.infos[key])
            def pages(self, key, checkpoint=None):
                owner.reads.append(key)
                if checkpoint:
                    checkpoint({"stage": "preparing", "sourceKey": key, "sourcePage": 1})
                verifier = PageReconciler()
                for page in deepcopy(owner.pages[key]):
                    verifier.consume(page, request_cursor=verifier.expected_cursor)
                    yield page
                if verifier.result() != owner.infos[key]["expected"]:
                    raise AiError("合成Reader完整核验失败", "conflict", 409)
        self.reader_type = SyntheticReader
        self.contexts = [patch.object(export, "Reader", SyntheticReader),
            patch.object(export.business_evidence, "get_run", side_effect=lambda *args: self.evidence),
            patch.object(export.business_reports, "content", side_effect=lambda *args: deepcopy(self.value)),
            patch.object(export.business_reports, "validate_review", side_effect=lambda *args: deepcopy(self.value))]
        for context in self.contexts:
            context.start(); self.addCleanup(context.stop)
        self.configure()

    def configure(self, count=1, *, v2=True, windows=("current",), market=False, rows=None):
        self.sources.clear(); self.pages.clear(); self.infos.clear(); self.reads.clear(); self.states.clear()
        for index in range(count):
            for window in windows:
                key = f"source-{index}-{window}"
                page, _ = fixture(window, rows=rows)
                query = {"platform": "京东", "shop": f"合成店{index}", "dataset": "promotion",
                    "startDate": "2026-09-01", "endDate": "2026-09-02", "window": window}
                page["filters"]["shop"] = query["shop"]
                page["sourceRef"] = key
                for item in page["items"]:
                    item["shopName"] = query["shop"]
                if market:
                    query = {k: v for k, v in query.items() if k not in {"shop", "dataset"}}
                    query.update(category="合成类目", scope="POP", rankingDimension="SKU", priceBandFilter="全部")
                    page.update(source="market_daily_top", sourceDataset="market_daily_top")
                    page["filters"] = {**query, "periods": page["filters"]["periods"], "shop": ""}
                    page["filters"].pop("startDate"); page["filters"].pop("endDate")
                    for item in page["items"]:
                        item["shopName"] = ""
                page["pageEvidence"]["sha256"] = digest(page["items"])
                # The immutable ledger stores canonical JSON, so key iteration
                # order must match actual persisted pages and checkpoints.
                page = json.loads(canonical(page))
                verifier = PageReconciler(); verifier.consume(page)
                self.sources.append({"key": key, "domain": "market" if market else "netshop", "query": query})
                self.pages[key] = [page]
                self.infos[key] = {"metadata": {"sourceRevision": "synthetic-1", "coverage": page["coverage"]},
                    "pageCount": 1, "expected": verifier.result()}
                self.states[key] = {"metadata": self.infos[key]["metadata"], "pageCount": 1, "verifier": deepcopy(verifier.__dict__)}
        plan = {"schemaVersion": "business-evidence-v2", "sourceCount": len(self.sources)} if v2 else {"schemaVersion": "business-evidence-v1", "sources": self.sources}
        self.evidence = SimpleNamespace(id="evidence-1", status="sealed", version=7, plan_json=canonical(plan), state_json=canonical(self.states))
        snapshot = {"schemaVersion": "business-report-v1", "evidenceRunId": self.evidence.id,
            "evidenceVersion": 7, "evidencePlanDigest": digest(self.evidence.plan_json), "scope": {"shop": "合成分析"}, "question": "合成报告"}
        if v2:
            snapshot.update(executionProfile="business-agent-reference-v2", evidenceProtocol="reference-v2", catalogDigest="c"*64, sealedDigest="d"*64)
        self.report = SimpleNamespace(id="report-1", owner_email=self.principal.email, scope_json="null", snapshot_json=canonical(snapshot),
            workflow=SimpleNamespace(status="completed"))

    def test_screening_summary_opt_in_v6_and_original_v4_overview_order(self):
        from . import business_screening_export
        snapshot = json.loads(self.report.snapshot_json)
        snapshot["executionProfile"] = "business-agent-screening-reference-v1"
        self.report.snapshot_json = canonical(snapshot)
        self.value["diagnosis"]["summary"] = "仅合成经营结论"
        self.value["screening"] = {"limitations": ["候选不证明因果"], "readProofs": {}}
        with patch.object(export.business_reports, "is_v2_snapshot", return_value=True), patch.object(business_screening_export, "metadata", return_value={}), patch.object(business_screening_export, "append"):
            keys = {}
            for version in (4, 6):
                with export.prepare_volumes(self.report, self.principal, renderer_version=version) as prepared:
                    keys[version] = [t.key for t in prepared.tables]
                    self.assertEqual(prepared.renderer_version, version)
            self.assertEqual(keys[4][0], "overview")
            self.assertNotIn("business-summary", keys[4])
            self.assertEqual(keys[6][0], "business-summary")
            self.assertGreater(keys[6].index("overview"), keys[6].index("actions"))
            self.assertEqual(set(keys[6])-set(keys[4]), {"business-summary"})

    def test_sealed_source_table_extraction_keeps_v4_v6_html_xlsx_bytes(self):
        """Pre-extraction golden bytes with a fixed ZIP creation clock."""
        from . import business_screening_export
        snapshot = json.loads(self.report.snapshot_json)
        snapshot["executionProfile"] = "business-agent-screening-reference-v1"
        self.report.snapshot_json = canonical(snapshot)
        self.value["diagnosis"]["summary"] = "仅合成经营结论"
        self.value["screening"] = {"limitations": ["候选不证明因果"], "readProofs": {}}
        golden = {
            4: ("d7aaccc9130c69fbff4bda623af3ca0e304a70e10bf7fe746366e64c367ebd1b",
                "cecee04a7384a1b2159c5e4b179d9e544ade44e2cee6e32ee233c8606cca9c09", 14),
            6: ("2ef0f39bdd3557f9688ab55b3a478af51d6c6ea9fd5a6a09a54a3f15f776fb76",
                "7d51255cb51c09942c89022006f2d2853af740c7d34a8cd304196739889d3fce", 15),
        }
        fixed_clock = time.struct_time((2026, 1, 1, 0, 0, 0, 3, 1, -1))
        with (patch.object(export.business_reports, "is_v2_snapshot", return_value=True),
                patch.object(business_screening_export, "metadata", return_value={}),
                patch.object(business_screening_export, "append"),
                patch.object(zipfile.time, "localtime", return_value=fixed_clock)):
            for version in (4, 6):
                with export.prepare_volumes(self.report, self.principal, renderer_version=version) as prepared:
                    outputs = self.outputs(prepared)
                    manifest = export.build_volumes(prepared, outputs)
                self.assertEqual(manifest["sourceTableCount"], golden[version][2])
                self.assertEqual(manifest["volumeCount"], 1)
                self.assertEqual(hashlib.sha256(outputs[0].html.getvalue()).hexdigest(), golden[version][0])
                self.assertEqual(hashlib.sha256(outputs[0].xlsx.getvalue()).hexdigest(), golden[version][1])
                keys = [table["key"] for table in manifest["tables"]]
                self.assertEqual(keys.count("raw-source-0-current"), 1)
                self.assertEqual(sum(key.startswith("analysis-") for key in keys), 8)

    def outputs(self, prepared):
        return [VolumeStreams(xlsx=BytesIO(), html=BytesIO()) for _ in range(prepared.plan["volumeCount"])]

    def test_large_catalog_all_tables_render_without_truncation(self):
        self.configure(13)
        with export.prepare_volumes(self.report, self.principal) as prepared:
            self.assertEqual(len(prepared.tables), 122)
            self.assertEqual(prepared.plan["volumeCount"], 2)
            self.assertEqual(sum(t.key.startswith("raw-") for t in prepared.tables), 13)
            self.assertEqual(sum(t.key.startswith("analysis-") for t in prepared.tables), 104)
            expected_rows = sum(t.row_count for t in prepared.tables)
            outputs = self.outputs(prepared)
            manifest = export.build_volumes(prepared, outputs)
            self.assertEqual((manifest["status"], manifest["totalRows"], manifest["sourceTableCount"]), ("complete", expected_rows, 122))
            self.assertEqual(manifest["evidenceDigest"], "d"*64)
            self.assertEqual(manifest["rendererVersion"], 4)
            for pair, volume in zip(outputs, manifest["volumes"]):
                for kind in ("html", "xlsx"):
                    stream = getattr(pair, kind)
                    data = stream.read()
                    self.assertEqual(hashlib.sha256(data).hexdigest(), volume["files"][kind]["sha256"])
                    self.assertEqual(len(data), volume["files"][kind]["bytes"])
                pair.xlsx.seek(0)
                with ZipFile(pair.xlsx) as workbook:
                    self.assertIsNone(workbook.testzip())
            self.assertEqual(set(self.reads), set(self.pages))

    def test_market_query_has_no_shop_and_market_page_has_empty_shop(self):
        self.configure(market=True)
        self.assertNotIn("shop", self.sources[0]["query"])
        self.assertEqual(self.pages[self.sources[0]["key"]][0]["filters"]["shop"], "")
        with export.prepare_volumes(self.report, self.principal) as prepared:
            raw = next(t for t in prepared.tables if t.key.startswith("raw-"))
            self.assertEqual(raw.row_count, 2)
            manifest = export.build_volumes(prepared, self.outputs(prepared))
            self.assertEqual(manifest["status"], "complete")

    def test_all_comparison_windows_and_daily_current_only(self):
        self.configure(windows=("current", "previous", "yearAgo"))
        with export.prepare_volumes(self.report, self.principal) as prepared:
            self.assertEqual(len(prepared.tables), 30)  # five headers + three raw + 7*3 + daily
            titles = [table.title for table in prepared.tables]
            self.assertEqual(sum("_同比" in title for title in titles), 7)
            self.assertEqual(sum("_环比" in title for title in titles), 7)
            self.assertEqual(sum("_逐日_" in title for title in titles), 1)

    def test_small_policy_splits_same_table_with_exact_boundaries(self):
        with export.prepare_volumes(self.report, self.principal, max_tables=3, max_rows=1) as prepared:
            manifest = export.build_volumes(prepared, self.outputs(prepared))
            fragments = [part for volume in manifest["volumes"] for part in volume["tables"] if part["key"] == "overview"]
            self.assertGreater(len(fragments), 1)
            self.assertEqual([part["rowOffset"] for part in fragments], list(range(len(fragments))))
            self.assertTrue(all(part["rowLimit"] == 1 for part in fragments))

    def test_empty_source_is_retained(self):
        self.configure(rows=[])
        with export.prepare_volumes(self.report, self.principal) as prepared:
            raw = next(t for t in prepared.tables if t.key.startswith("raw-"))
            self.assertEqual(raw.row_count, 0)
            manifest = export.build_volumes(prepared, self.outputs(prepared))
            self.assertTrue(any(table["key"] == raw.key and table["rowCount"] == 0 for table in manifest["tables"]))

    def test_old_build_explicitly_rejects_v2_before_writer_or_reader(self):
        for renderer in (1, 2, 3):
            with patch.object(export, "write_pair") as writer, patch.object(export, "Reader") as reader, self.assertRaises(AiError):
                export.build(self.report, self.principal, BytesIO(), BytesIO(), renderer_version=renderer)
            writer.assert_not_called(); reader.assert_not_called()

    def test_draft_and_formal_review_paths_and_parent_binding(self):
        self.report.workflow.status = "waiting_review"
        with self.assertRaises(AiError):
            with export.prepare_volumes(self.report, self.principal):
                self.fail("unreviewed formal export admitted")
        with export.prepare_volumes(self.report, self.principal, draft=True) as prepared:
            self.assertEqual(prepared.metadata["status"], "待复核草稿")
        self.evidence.version += 1
        with self.assertRaises(AiError):
            with export.prepare_volumes(self.report, self.principal, draft=True):
                self.fail("changed parent admitted")

    def test_reader_failure_prevents_any_render(self):
        with patch.object(self.reader_type, "pages", side_effect=AiError("wrong domain identity")), \
                patch("business_analysis.volume_files.render") as render, self.assertRaises(AiError):
            with export.prepare_volumes(self.report, self.principal):
                self.fail("invalid reader admitted")
        render.assert_not_called()

    def test_context_is_one_shot_and_failure_returns_no_manifest(self):
        with export.prepare_volumes(self.report, self.principal) as prepared:
            outputs = self.outputs(prepared)
            with self.assertRaises(AiError):
                export.build_volumes(prepared, outputs, max_file_bytes=32)
            with self.assertRaises(AiError):
                export.build_volumes(prepared, self.outputs(prepared))
        with self.assertRaises(AiError):
            export.build_volumes(prepared, outputs)
        self.assertTrue(all(not stream.closed for pair in outputs for stream in (pair.html, pair.xlsx)))

    def test_tampered_plan_rejected_before_output(self):
        with export.prepare_volumes(self.report, self.principal) as prepared:
            outputs = self.outputs(prepared)
            prepared.plan["totalRows"] = -1
            with self.assertRaises(AiError):
                export.build_volumes(prepared, outputs)
            self.assertTrue(all(not stream.getvalue() for pair in outputs for stream in (pair.html, pair.xlsx)))

    def test_preparation_scope_report_and_alias_rebinding_rejected(self):
        def scope(prepared):
            prepared.metadata["scope"]["shop"] = "其他店铺"
        def report(prepared):
            object.__setattr__(prepared, "report_id", "other-report")
            prepared.metadata["reportId"] = "other-report"
            prepared.plan["reportId"] = "other-report"
        def policy(prepared):
            prepared.policy["max_rows"] = 1
        def calculator(prepared):
            object.__setattr__(prepared, "calculator", {"reportId": prepared.report_id})
        for change in (scope, report, policy, calculator):
            with export.prepare_volumes(self.report, self.principal) as prepared:
                outputs = self.outputs(prepared)
                change(prepared)
                with self.assertRaises(AiError):
                    export.build_volumes(prepared, outputs)
                self.assertTrue(all(not stream.getvalue() for pair in outputs for stream in (pair.html, pair.xlsx)))

    def test_binding_hashes_many_wide_tables_individually(self):
        # 156 * 160 columns exceed the JSON helper's combined node bound;
        # each table remains bounded and row iterators must never be advanced.
        class UnreadableRows:
            def __iter__(self):
                raise AssertionError("binding read source rows")
        tables = tuple(Table(f"t{i}", f"表{i}", "合成宽表", tuple(Column(f"c{j}", f"列{j}") for j in range(160)), UnreadableRows(), 0) for i in range(156))
        prepared = export.PreparedVolumes({}, tables, {}, None, "report", "a"*64, {})
        self.assertEqual(export._prepared_binding(prepared), prepared._binding_digest)
        object.__setattr__(tables[-1].columns[-1], "label", "变化列")
        self.assertNotEqual(export._prepared_binding(prepared), prepared._binding_digest)

    def test_legacy_renderer_keeps_metadata_and_writer_contract(self):
        self.configure(v2=False)
        for renderer in (1, 2, 3):
            xlsx, html = BytesIO(), BytesIO()
            result = export.build(self.report, self.principal, xlsx, html, renderer_version=renderer)
            self.assertEqual(len(result["tables"]), 14)
            self.assertIn(b"business-files-v1", html.getvalue())
            self.assertNotIn(b"volumeDelivery", html.getvalue())
            with ZipFile(xlsx) as workbook:
                self.assertIsNone(workbook.testzip())
        with self.assertRaises(AiError):
            with export.prepare_volumes(self.report, self.principal):
                self.fail("v1 admitted to new internal entry")

    def test_v1_complete_table_content_matches_pre_reader_golden(self):
        # Golden independently computed with the preceding committed package()
        # against the same canonical immutable page/checkpoint projections.
        self.configure(v2=False)
        with export.package(self.report, self.principal, draft=False, renderer_version=3) as (metadata, tables, calculator):
            value = {"metadata": metadata, "calculator": calculator, "tables": [
                {"key": table.key, "title": table.title, "note": table.note,
                 "columns": [vars(column) for column in table.columns], "rowCount": table.row_count,
                 "rows": list(table.rows)} for table in tables]}
            self.assertEqual(digest(value), "fa82af3c6cdfe3744f2e8c6030c5caf106d26e4e284bbecd60987a33c090e59b")

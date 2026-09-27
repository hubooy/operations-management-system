"""Actual approved five-Agent HTML/XLSX temporary pair, without file runs."""
import io
import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
import zipfile
from business_analysis.report_files import Column, Table

from django import test as djtest
from access_control.models import AppUser

from . import business_promotion_formal_export as service
from . import models as m
from . import test_business_promotion_approved_content as fixtures
from .policy import AiError, canonical, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionFormalExportTests(djtest.TransactionTestCase):
    user = fixtures.PromotionApprovedContentTests.user
    call = fixtures.PromotionApprovedContentTests.call
    collect_body = fixtures.PromotionApprovedContentTests.collect_body
    bundle = fixtures.PromotionApprovedContentTests.bundle
    input_for = fixtures.PromotionApprovedContentTests.input_for
    insert = fixtures.PromotionApprovedContentTests.insert
    seed = fixtures.PromotionApprovedContentTests.seed
    setUp = fixtures.PromotionApprovedContentTests.setUp
    request_body = fixtures.PromotionApprovedContentTests.request_body
    current_catalog = fixtures.PromotionApprovedContentTests.current_catalog
    create_fixed_report = fixtures.PromotionApprovedContentTests.create_fixed_report
    base = fixtures.PromotionApprovedContentTests.base
    read = fixtures.PromotionApprovedContentTests.read
    append = fixtures.PromotionApprovedContentTests.append
    package = fixtures.PromotionApprovedContentTests.package
    promotion = fixtures.PromotionApprovedContentTests.promotion
    complete = fixtures.PromotionApprovedContentTests.complete
    running_job = fixtures.PromotionApprovedContentTests.running_job
    five_completed = fixtures.PromotionApprovedContentTests.five_completed
    approved = fixtures.PromotionApprovedContentTests.approved

    def test_trial_source_projection_adds_readable_scope_without_changing_v7_default(self):
        report = self.five_completed()
        self.approved(report)
        with patch.object(service.file_tables.runtime.transport, "catalog",
                side_effect=self.current_catalog):
            with service.file_tables.open_tables(report.id, self.admin,
                    draft=False) as (metadata, _):
                fixed = metadata.value["reportBinding"]
                with service._source_tables(report.id, self.admin, fixed,
                        None) as (old_tables, old_proof):
                    old_keys = [table.key for table in old_tables]
                with service._source_tables(report.id, self.admin, fixed,
                        None, trial=True) as (trial_tables, trial_proof):
                    by_key = {table.key: table for table in trial_tables}
                    self.assertEqual([row[0] for row in
                        by_key["promotion-trial-source-scope"].rows],
                        [source["key"] for source in service.Reader(
                            service.business_evidence.get_run(
                                json.loads(report.snapshot_json)["evidenceRunId"],
                                self.admin), self.admin).sources])
                    self.assertEqual(by_key["promotion-trial-boundaries"].row_count, 6)
                    self.assertEqual(trial_proof["sourceTableCount"],
                        old_proof["sourceTableCount"] + 2)
                    self.assertEqual([table.key for table in trial_tables
                        if table.key not in {"promotion-trial-source-scope",
                            "promotion-trial-boundaries"}], old_keys)

    def test_actual_approved_pair_contains_both_full_views_and_reviewed_content(self):
        report = self.five_completed(promotion_reference=True, native_reference=True)
        self.approved(report)
        before = m.AiBusinessFileRun.objects.count()
        with patch.object(service.file_tables.runtime.transport, "catalog", side_effect=self.current_catalog), patch(
                "ai_assistant.provider.turn") as model, patch("ai_assistant.transport.execute_tool") as remote:
            with service.open_files(report.id, self.admin) as prepared:
                receipt = prepared.manifest
                self.assertEqual(receipt["rendererVersion"], 7)
                self.assertFalse(receipt["deliveryAuthorized"])
                self.assertFalse(receipt["registeredRenderer"])
                self.assertFalse(receipt["authorityVerified"])
                self.assertEqual(receipt["receiptDigest"], digest({key: value for key, value in receipt.items()
                    if key != "receiptDigest"}))
                self.assertEqual(len(receipt["fileProof"]["tables"]), 2)
                self.assertFalse(receipt["fileProof"]["tableExpensesAreAdditive"])
                source_keys = set(m.AiBusinessEvidenceSource.objects.filter(
                    run_id=json.loads(report.snapshot_json)["evidenceRunId"]).values_list("source_key", flat=True))
                rendered_keys = [item["key"] for item in receipt["renderedTables"]]
                self.assertIn("sources", rendered_keys)
                self.assertEqual({key[4:] for key in rendered_keys if key.startswith("raw-")}, source_keys)
                self.assertGreater(sum(key.startswith("analysis-") for key in rendered_keys), 0)
                self.assertEqual(receipt["sealedSourceCount"], len(source_keys))
                self.assertEqual(receipt["sealedSourceTableCount"],
                    1 + len(source_keys) + sum(key.startswith("analysis-") for key in rendered_keys))
                self.assertEqual(receipt["fileProof"]["tables"][0]["spendTotals"]["current"]["value"],
                    receipt["fileProof"]["tables"][1]["spendTotals"]["current"]["value"])
                html = prepared.path("html").read_bytes()
                xlsx = prepared.path("xlsx").read_bytes()
                self.assertIn("关键词与推广SKU".encode(), html)
                self.assertIn("调整规划与证据".encode(), html)
                self.assertEqual(len(html), receipt["files"]["html"]["bytes"])
                self.assertEqual(len(xlsx), receipt["files"]["xlsx"]["bytes"])
                with zipfile.ZipFile(io.BytesIO(xlsx)) as archive:
                    self.assertIn("teruisi-manifest.json", archive.namelist())
                    self.assertIn(b'Extension="json" ContentType="application/json"',
                        archive.read("[Content_Types].xml"))
                    embedded = json.loads(archive.read("teruisi-manifest.json"))
                    self.assertEqual([item["key"] for item in embedded["tables"][-2:]],
                        ["promotion-keyword_sku", "promotion-keyword_sku_context"])
                    self.assertEqual([item["rowCount"] for item in embedded["tables"][-2:]],
                        [item["rowCount"] for item in receipt["fileProof"]["tables"]])
                first_path = prepared.path("html")
            with self.assertRaises(AiError): prepared.path("html")
            self.assertFalse(first_path.exists())
        model.assert_not_called(); remote.assert_not_called()
        self.assertEqual(m.AiBusinessFileRun.objects.count(), before)

    def test_over_120_tables_is_all_or_nothing_for_single_pair(self):
        report = self.five_completed()
        self.approved(report)
        @contextmanager
        def over_capacity(*_):
            columns = (Column("value", "值"),)
            tables = tuple(Table(f"source-{index}", f"来源{index}", "合成容量边界", columns,
                [], 0) for index in range(114))
            yield tables, {"sourceCount": 1, "sourcesDigest": "a"*64,
                "sourceTableCount": len(tables)}
        with patch.object(service.file_tables.runtime.transport, "catalog", side_effect=self.current_catalog), patch.object(
                service, "_source_tables", over_capacity), patch.object(service.report_files, "write_pair") as writer:
            with self.assertRaises(AiError) as caught:
                with service.open_files(report.id, self.admin): self.fail("overfull pair escaped")
        self.assertEqual(caught.exception.status, 413)
        writer.assert_not_called()

    def test_unapproved_or_conflicted_report_never_writes_formal_files(self):
        report = self.five_completed()
        with patch.object(service.file_tables.runtime.transport, "catalog", side_effect=self.current_catalog), patch.object(
                service.report_files, "write_pair") as writer, self.assertRaises(AiError):
            with service.open_files(report.id, self.admin): self.fail("unapproved escaped")
        writer.assert_not_called()
        self.approved(report, decision="reject")
        with patch.object(service.file_tables.runtime.transport, "catalog", side_effect=self.current_catalog), patch.object(
                service.report_files, "write_pair") as writer, self.assertRaises(AiError):
            with service.open_files(report.id, self.admin): self.fail("rejected escaped")
        writer.assert_not_called()

    def test_partial_temp_output_and_late_revocation_publish_nothing(self):
        report = self.five_completed()
        self.approved(report)
        before = m.AiBusinessFileRun.objects.count()
        paths = []
        def partial(xlsx, html, **_):
            paths.extend([Path(xlsx.name), Path(html.name)])
            xlsx.write(b"partial-xlsx"); html.write(b"partial-html")
            raise RuntimeError("synthetic writer failure")
        with patch.object(service.file_tables.runtime.transport, "catalog", side_effect=self.current_catalog), patch.object(
                service.report_files, "write_pair", side_effect=partial), self.assertRaisesRegex(RuntimeError, "writer failure"):
            with service.open_files(report.id, self.admin): self.fail("partial escaped")
        self.assertTrue(paths and all(not path.exists() for path in paths))
        with patch.object(service.file_tables.runtime.transport, "catalog", side_effect=self.current_catalog), self.assertRaises(AiError):
            with service.open_files(report.id, self.admin) as prepared:
                self.assertTrue(prepared.path("html").exists())
                AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        self.assertEqual(m.AiBusinessFileRun.objects.count(), before)

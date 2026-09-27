"""Synthetic 575,095-row scale and failure-closed volume staging tests."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import time
import tracemalloc
from unittest import TestCase

from .contracts import AnalysisContractError, canonical, digest
from .report_files import Column
from . import report_composition_volume_stream_v1 as volume


SOURCE = "promotion-current"
COLUMNS = (Column("sourceKey", "来源键"),
    Column("rowIndex", "来源连续行号", "integer"),
    Column("spendCents", "推广费分", "integer"))


def rows(count):
    for index in range(count):
        yield (SOURCE, index, index % 1000)


def inputs(count):
    base_body = {"schemaVersion":
            "business-report-composition-owning-preview-v1",
        "reportBinding": {"reportId": "synthetic-sealed-v2"},
        "planDigest": "c" * 64,
        "sourceEvidenceDigests": {SOURCE: "a" * 64},
        "sourceRevisions": {SOURCE: "synthetic-revision"},
        "promotionSourceKeys": [SOURCE],
        "pairedBytesVerified": True, "tableCount": 13,
        "published": False, "agentReadPersisted": False}
    base = {**base_body, "resultDigest": digest(base_body)}
    sha = hashlib.sha256()
    for row in rows(count):
        sha.update((canonical(list(row)) + "\n").encode("utf-8"))
    source_body = {"schemaVersion": volume.SCHEMA,
        "reportBindingDigest": digest(base["reportBinding"]),
        "planDigest": base["planDigest"], "sourceKey": SOURCE,
        "sourceEvidenceDigest": base["sourceEvidenceDigests"][SOURCE],
        "sourceRevision": base["sourceRevisions"][SOURCE],
        "rowCount": count, "rowDigest": sha.hexdigest()}
    source = {**source_body, "manifestDigest": digest(source_body)}
    return base, source


class Sink:
    def __init__(self):
        self.volumes = []
        self.final = None
        self.aborted = False

    def stage(self, metadata, html_path, xlsx_path):
        assert Path(html_path).is_file() and Path(xlsx_path).is_file()
        assert Path(html_path).stat().st_size == metadata["htmlBytes"]
        assert Path(xlsx_path).stat().st_size == metadata["xlsxBytes"]
        self.volumes.append(metadata)

    def abort(self):
        self.aborted = True
        self.volumes.clear()
        self.final = None

    def complete(self, manifest):
        self.final = manifest


class ReportCompositionVolumeTests(TestCase):
    def test_reference_scale_575095_rows_no_truncation_and_bounded_peak(self):
        count = 575_095
        base, source = inputs(count)
        sink = Sink()
        tracemalloc.start()
        start = time.perf_counter()
        try:
            result = volume.build_candidate(base, source, COLUMNS,
                rows(count), sink, lambda *_: True, enabled=True)
            elapsed = time.perf_counter() - start
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertFalse(sink.aborted)
        self.assertEqual(result, sink.final)
        self.assertEqual(result["sourceRowCount"], count)
        self.assertEqual(result["sourceRowDigest"], source["rowDigest"])
        self.assertEqual(result["volumeCount"], 12)
        self.assertEqual(sum(item["rowCount"] for item in sink.volumes), count)
        self.assertEqual(sink.volumes[-1]["lastRowIndex"], count-1)
        self.assertEqual(result["status"], "staged_unpublished")
        self.assertFalse(result["agentReadPersisted"])
        self.assertFalse(result["registeredRenderer"])
        self.assertLess(peak, 192 * 1024 * 1024)
        print(f"synthetic_575095_rows elapsed_seconds={elapsed:.2f} "
            f"tracemalloc_peak_mib={peak/1024/1024:.2f} "
            f"volumes={result['volumeCount']} "
            f"html_bytes={sum(v['htmlBytes'] for v in sink.volumes)} "
            f"xlsx_bytes={sum(v['xlsxBytes'] for v in sink.volumes)}")

    def test_default_closed_source_drift_and_wrong_complete_root_abort(self):
        base, source = inputs(4)
        sink = Sink()
        with self.assertRaises(AnalysisContractError):
            volume.build_candidate(base, source, COLUMNS, rows(4),
                sink, lambda *_: True)
        self.assertEqual(sink.volumes, [])
        with self.assertRaises(AnalysisContractError):
            volume.build_candidate(base, source, COLUMNS, rows(4),
                sink, lambda *_: False, enabled=True)
        self.assertEqual(sink.volumes, [])
        different_domain = deepcopy(base)
        different_domain["promotionSourceKeys"] = []
        different_domain["resultDigest"] = digest({key: value for key,
            value in different_domain.items() if key != "resultDigest"})
        with self.assertRaises(AnalysisContractError):
            volume.build_candidate(different_domain, source, COLUMNS,
                rows(4), sink, lambda *_: True, enabled=True)
        self.assertEqual(sink.volumes, [])
        wrong = deepcopy(source)
        wrong["rowDigest"] = "0" * 64
        wrong["manifestDigest"] = digest({key: value for key, value in
            wrong.items() if key != "manifestDigest"})
        with self.assertRaises(AnalysisContractError):
            volume.build_candidate(base, wrong, COLUMNS, rows(4),
                sink, lambda *_: True, enabled=True, max_volume_rows=2)
        self.assertTrue(sink.aborted)
        self.assertIsNone(sink.final)
        self.assertEqual(sink.volumes, [])

    def test_gap_cross_source_and_final_revision_change_abort(self):
        base, source = inputs(4)
        for bad in ((SOURCE, 0, 0), (SOURCE, 2, 1)):
            sink = Sink()
            with self.assertRaises(AnalysisContractError):
                volume.build_candidate(base, source, COLUMNS,
                    iter([bad]), sink, lambda *_: True,
                    enabled=True, max_volume_rows=2)
            self.assertTrue(sink.aborted)
        sink, checks = Sink(), 0
        def rev(*_):
            nonlocal checks
            checks += 1
            return checks == 1
        with self.assertRaises(AnalysisContractError):
            volume.build_candidate(base, source, COLUMNS, rows(4),
                sink, rev, enabled=True, max_volume_rows=2)
        self.assertEqual(checks, 2)
        self.assertTrue(sink.aborted)
        self.assertEqual(sink.volumes, [])

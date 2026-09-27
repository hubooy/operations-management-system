"""Create-only synthetic renderer-10 HTML/XLSX scale rehearsal (never Office).

The values and approval roots come from test fixtures, never owning services.
Usage: python tools/business-budget-v10-static-scale.py NEW_DIR --rows 575095
Only a complete-manifest.json plus scale-evidence.json marks a valid run.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import ctypes
import hashlib
import json
from pathlib import Path
import sys
import time
import tracemalloc
import xml.parsers.expat
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from business_analysis import (budget_excel, promotion_budget_v10, promotion_trial_table_schema,
    volume_delivery, volume_files, volume_plan)
from business_analysis.contracts import canonical, digest
from business_analysis.report_files import Column, Table, column_name
from business_analysis.test_promotion_budget_v10 import roots
from business_analysis.test_promotion_volume_delivery import valid as promotion_fixture


REPORT_ID = "promotion-1"
EVIDENCE_DIGEST = "d" * 64
BINDING_DIGEST = "8" * 64
DEFAULT_ROWS = 575_095  # synthetic equivalent of 281,759 + 293,336 references
MAX_XML_BYTES = 1024 * 1024 * 1024
CHUNK = 1024 * 1024


def _file(path):
    sha, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            sha.update(block)
            size += len(block)
    return {"bytes": size, "sha256": sha.hexdigest()}


def _working_set():
    """Windows process peak working set, or None when unavailable."""
    if sys.platform != "win32":
        return None
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("pageFaultCount", ctypes.c_ulong)] + [
            (name, ctypes.c_size_t) for name in ("peakWorkingSetSize", "workingSetSize",
                "quotaPeakPagedPoolUsage", "quotaPagedPoolUsage",
                "quotaPeakNonPagedPoolUsage", "quotaNonPagedPoolUsage",
                "pagefileUsage", "peakPagefileUsage")]
    result = Counters()
    result.cb = ctypes.sizeof(result)
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    process = kernel.GetCurrentProcess()
    psapi = ctypes.windll.psapi
    psapi.GetProcessMemoryInfo.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)
    if psapi.GetProcessMemoryInfo(process, ctypes.byref(result), result.cb):
        return result.peakWorkingSetSize
    return None


class SyntheticRows:
    """One-pass deterministic rows with reproducible entropy and XML width."""
    def __init__(self, count, *, suffix_bytes=68):
        self.count, self.suffix_bytes = count, suffix_bytes
        self.iterations = self.consumed = 0
        self.sha = hashlib.sha256()

    def __iter__(self):
        self.iterations += 1
        if self.iterations != 1:
            raise AssertionError("synthetic source was read more than once")
        for index in range(1, self.count + 1):
            a = hashlib.sha256(f"synthetic-v10-a:{index}".encode()).hexdigest()
            row = ["2026-08-16", str(index), "SKU-" + a[:16],
                a[16:32], "合成设备", index % 1901, index % 777,
                index % 880001,
                "仅作结构压力验收-" + a[32:36] + "x" * self.suffix_bytes]
            self.sha.update((canonical(row) + "\n").encode("utf-8"))
            self.consumed += 1
            yield row


def _small(key, title, values):
    return Table(key, title, "合成演练；不代表来源、审批或业务结论。",
        (Column("value", "合成值"),), [[values]], 1)


def synthetic_tables(rows, suffix_bytes=68):
    source = SyntheticRows(rows, suffix_bytes=suffix_bytes)
    large = Table("synthetic-promotion-raw", "合成推广原始明细",
        "仅用于 renderer 10 文件结构/容量压力验收；不是平台来源事实。",
        (Column("date", "合成日期"), Column("rowId", "行号"),
         Column("sku", "合成 SKU"), Column("keyword", "合成关键词"),
         Column("category", "合成品类"),
         Column("spendCents", "合成推广分", "integer"),
         Column("clicks", "合成点击", "integer"),
         Column("gmvCents", "合成归因金额分", "integer"),
         Column("nonAuthority", "非业务权威标记")), source, rows)
    action = _small("promotion-approved-actions-v1", "合成行动表", "synthetic-only")
    scope = _small("promotion-trial-source-scope", "合成来源范围", "no-owning-source")
    boundary = _small("promotion-trial-boundaries", "合成能力边界", "candidate-only")
    promotion = (_small("promotion-keyword_sku", "合成词货表", "no-platform-data"),
        _small("promotion-keyword_sku_context", "合成词货上下文", "no-platform-data"))
    return source, action, (scope, boundary), large, promotion


def prepared(rows, *, max_rows=1_000_000, suffix_bytes=68,
             renderer_version=10):
    if renderer_version not in (10, 11):
        raise ValueError("synthetic budget renderer must be 10 or 11")
    source, action, scope, large, promotion = synthetic_tables(rows, suffix_bytes)
    _, promotion_proof, _, _, _ = promotion_fixture()
    promotion_proof = {**promotion_proof, "reportId": REPORT_ID}
    promotion_proof["proofDigest"] = digest({key: value for key, value in
        promotion_proof.items() if key != "proofDigest"})
    fixture = roots(with_budget=True)
    trial = fixture["trial_proof"]
    trial.update(contentDtoDigest=promotion_proof["contentDtoDigest"],
        humanReviewDigest=promotion_proof["humanReviewDigest"],
        promotionFileProofDigest=promotion_proof["proofDigest"],
        actionRowDigest=hashlib.sha256((canonical(action.rows[0]) + "\n").encode()).hexdigest())
    fixture["approved_binding"]["humanReview"]["reviewDigest"] = trial["humanReviewDigest"]
    fixture["approved_dto_digest"] = trial["contentDtoDigest"]

    def project():
        trial["proofDigest"] = digest({key: value for key, value in trial.items()
            if key != "proofDigest"})
        candidate = promotion_budget_v10.project(**fixture)
        tables = (action, *scope, large, *candidate.tables, *promotion)
        request = volume_files.request_for(tables, report_id=REPORT_ID,
            evidence_digest=EVIDENCE_DIGEST, renderer_version=renderer_version)
        plan = volume_plan.build(request, native_budget_sheets=3,
            max_rows=max_rows)
        return candidate, tables, request, plan

    _, tables, _, plan = project()
    trial["sourceDescriptorDigest"] = plan["sourceDescriptorDigest"]
    trial["tableSchemaDigest"] = promotion_trial_table_schema.digest_tables(tables)
    candidate, tables, request, plan = project()
    if (candidate.proof["tableSchemaDigest"] !=
            promotion_trial_table_schema.digest_tables(candidate.tables) or
            trial["tableSchemaDigest"] !=
            promotion_trial_table_schema.digest_tables(tables)):
        raise AssertionError("synthetic table declarations drifted")
    metadata = {"syntheticOnly": True, "owningSourceAuthorityVerified": False,
        "promotionFileProof": promotion_proof,
        "promotionTrialProof": dict(trial),
        "promotionBudgetProof": candidate.proof,
        "tableSchemaDigest": trial["tableSchemaDigest"]}
    return source, candidate, tables, request, plan, metadata


def inspect_xlsx(path, volume, *, expected_large_rows, budget_payload=None,
                 large_index=4):
    """Stream every ZIP member through CRC and Expat; never load large XML."""
    sheets = []
    expected_formulas = []
    if volume["nativeBudgetSheets"] == 3:
        if budget_payload is None:
            raise AssertionError("fixed budget payload missing for formula check")
        model_sheets, proof = budget_excel.build(budget_payload,
            volume["budgetCalculator"]["sheets"], formula_version=2)
        if proof != volume["budgetCalculator"]:
            raise AssertionError("native budget calculator proof changed")
        for model in model_sheets:
            sha, count = hashlib.sha256(), 0
            for (row, col), (_, formula, _) in sorted(model.cells.items()):
                if formula is not None:
                    sha.update((canonical([column_name(col) + str(row), formula]) + "\n").encode())
                    count += 1
            expected_formulas.append((count, sha.hexdigest()))
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or any(name.startswith("/") or ".." in name.split("/")
                or "externalLink" in name or "vbaProject" in name for name in names):
            raise AssertionError("unexpected XLSX OPC member")
        content_type = archive.read("[Content_Types].xml")
        if b'Extension="json" ContentType="application/json"' not in content_type:
            raise AssertionError("XLSX is missing JSON OPC ContentType")
        workbook = archive.read("xl/workbook.xml")
        if b'fullCalcOnLoad="1"' not in workbook:
            raise AssertionError("workbook does not request native recalc")
        embedded = json.loads(archive.read("teruisi-manifest.json"))
        if [item["rowDigest"] for item in embedded["tables"]] != [
                item["rowDigest"] for item in volume["tables"]]:
            raise AssertionError("embedded row digest differs from complete manifest")
        for name in names:
            info = archive.getinfo(name)
            if info.file_size > MAX_XML_BYTES:
                raise AssertionError("unexpected single XLSX member expansion")
            total = 0
            row_count = formula_count = 0
            formula_sha = hashlib.sha256()
            current_cell = None
            formula_parts = None
            parser = xml.parsers.expat.ParserCreate()
            if name.endswith(".xml") or name.endswith(".rels"):
                def opening(tag, attrs):
                    nonlocal row_count, formula_count, current_cell, formula_parts
                    if tag == "row":
                        row_count += 1
                    elif tag == "c":
                        current_cell = attrs.get("r")
                    elif tag == "f":
                        formula_count += 1
                        formula_parts = []
                def characters(value):
                    if formula_parts is not None:
                        formula_parts.append(value)
                def closing(tag):
                    nonlocal formula_parts, current_cell
                    if tag == "f":
                        formula_sha.update((canonical([current_cell,
                            "".join(formula_parts)]) + "\n").encode())
                        formula_parts = None
                    elif tag == "c":
                        current_cell = None
                parser.StartElementHandler = opening
                parser.CharacterDataHandler = characters
                parser.EndElementHandler = closing
            with archive.open(info) as member:
                for block in iter(lambda: member.read(CHUNK), b""):
                    total += len(block)
                    if name.endswith(".xml") or name.endswith(".rels"):
                        parser.Parse(block, False)
            if name.endswith(".xml") or name.endswith(".rels"):
                parser.Parse(b"", True)
            if total != info.file_size:
                raise AssertionError("ZIP member size changed")
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"):
                sheets.append({"name": name, "rowsIncludingHeaders": row_count,
                    "formulaCount": formula_count,
                    "formulaTextDigest": formula_sha.hexdigest(),
                    "xmlBytes": total})
        if len(sheets) != len(volume["tables"]) + volume["nativeBudgetSheets"]:
            raise AssertionError("XLSX sheet count differs from bound plan")
        if volume["nativeBudgetSheets"] == 3 and [
                (sheet["formulaCount"], sheet["formulaTextDigest"])
                for sheet in sheets[-3:]] != expected_formulas:
            raise AssertionError("native budget formula text differs from fixed calculator")
        if expected_large_rows and volume["volumeIndex"] == 1:
            large = sheets[large_index - 1]
            if large["rowsIncludingHeaders"] != expected_large_rows + 3:
                raise AssertionError("synthetic source worksheet row count changed")
    return sheets


def run(directory, *, rows=DEFAULT_ROWS, max_rows=1_000_000,
        suffix_bytes=68, deadline_seconds=1800, html_slim_v10=False,
        renderer_version=10):
    if (not 1 <= rows <= 1_000_000 or not 1 <= max_rows <= 1_000_000
            or not 0 <= suffix_bytes <= 128 or type(html_slim_v10) is not bool
            or renderer_version not in (10, 11)
            or renderer_version == 11 and html_slim_v10):
        raise ValueError("synthetic scale parameters exceed bounded contract")
    directory = Path(directory).resolve()
    if directory.exists():
        raise FileExistsError("output directory must be new")
    directory.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    tracemalloc.start()
    outputs, peak_temp_bytes = [], 0
    try:
        source, candidate, tables, request, plan, metadata = prepared(rows,
            max_rows=max_rows, suffix_bytes=suffix_bytes,
            renderer_version=renderer_version)
        def checkpoint(_):
            nonlocal peak_temp_bytes
            if time.monotonic() - started > deadline_seconds:
                raise TimeoutError("synthetic render exceeded deadline; no delivery receipt")
            peak_temp_bytes = max(peak_temp_bytes, sum(
                pair.xlsx.tell() + pair.html.tell() for pair in outputs))
        with ExitStack() as stack:
            for volume in plan["volumes"]:
                index = volume["volumeIndex"]
                xlsx = stack.enter_context((directory / f"volume-{index:03}.xlsx").open("x+b"))
                html = stack.enter_context((directory / f"volume-{index:03}.html").open("x+b"))
                outputs.append(volume_files.VolumeStreams(xlsx, html))
            full = volume_files.render(tables, outputs, report_id=REPORT_ID,
                evidence_digest=EVIDENCE_DIGEST, renderer_version=renderer_version, plan=plan,
                title="合成推广预算容量验收", metadata=metadata,
                offline_budget=candidate.offline_budget,
                excel_budget=candidate.excel_budget,
                max_rows=max_rows, checkpoint=checkpoint,
                html_slim_v10=html_slim_v10)
        render_seconds = time.monotonic() - started
        if source.iterations != 1 or source.consumed != rows or full["totalRows"] != rows + sum(
                table.row_count for table in tables if table.key != "synthetic-promotion-raw"):
            raise AssertionError("synthetic input was truncated or reread")
        large = next(table for table in full["tables"] if table["key"] == "synthetic-promotion-raw")
        if large["rowDigest"] != source.sha.hexdigest():
            raise AssertionError("full source row SHA differs from independent producer")
        compact, raw = volume_delivery.make(full, binding_digest=BINDING_DIGEST,
            attempt=1, draft=False, renderer_version=renderer_version,
            max_rows=max_rows)
        verified = volume_delivery.verify_full(compact, raw,
            binding_digest=BINDING_DIGEST, attempt=1, draft=False,
            report_id=REPORT_ID, evidence_digest=EVIDENCE_DIGEST,
            renderer_version=renderer_version, max_rows=max_rows)
        if verified != full:
            raise AssertionError("v10 complete manifest failed exact rebuild")
        descriptors = {(item["volumeIndex"], item["format"]): item
            for item in [*compact["files"], compact["manifestFile"]]}
        all_files = []
        sheet_results = []
        for volume in full["volumes"]:
            index = volume["volumeIndex"]
            for kind in ("html", "xlsx"):
                path = directory / f"volume-{index:03}.{kind}"
                actual = _file(path)
                expected = descriptors[index, kind]
                if actual != {key: expected[key] for key in ("bytes", "sha256")}:
                    raise AssertionError("actual v10 volume bytes differ from compact root")
                all_files.append({"volumeIndex": index, "format": kind, **actual})
            sheet_results.append({"volumeIndex": index,
                "sheets": inspect_xlsx(directory / f"volume-{index:03}.xlsx",
                    volume, expected_large_rows=min(rows, max_rows),
                    budget_payload=candidate.excel_budget if index == 1 else None)})
        if _file_bytes(raw) != {key: descriptors[0, "json"][key] for key in ("bytes", "sha256")}:
            raise AssertionError("full JSON manifest bytes differ from compact root")
        _, python_peak = tracemalloc.get_traced_memory()
        peak_temp_bytes = max(peak_temp_bytes, sum(item["bytes"] for item in all_files))
        evidence = {"schemaVersion": "business-budget-v10-static-scale-v1",
            "syntheticOnly": True, "owningSourceAuthorityVerified": False,
            "nativeExcelOpened": False, "formulaRecalculated": False,
            "rendererVersion": renderer_version,
            "htmlSlimV10": html_slim_v10,
            "htmlPayloadVersion": 2 if renderer_version == 11 or html_slim_v10 else 1,
            "reportId": REPORT_ID, "syntheticPromotionRows": rows,
            "totalRows": full["totalRows"], "volumeCount": full["volumeCount"],
            "sourceRowSha256": large["rowDigest"],
            "manifestDigest": full["manifestDigest"],
            "manifestFileSha256": descriptors[0, "json"]["sha256"],
            "files": all_files, "sheets": sheet_results,
            "renderSeconds": round(render_seconds, 3),
            "totalSeconds": round(time.monotonic() - started, 3),
            "pythonPeakTracedBytes": python_peak,
            "processPeakWorkingSetBytes": _working_set(),
            "peakTemporaryOutputBytes": peak_temp_bytes,
            "hardCaps": {"maxFileBytes": volume_delivery.MAX_FILE_BYTES,
                "maxDeliveryBytes": volume_delivery.MAX_DELIVERY_BYTES,
                "maxRowsPerFragment": max_rows,
                "dynamicByteSplitting": False},
            "completeManifestVerified": True, "allFileHashesVerified": True,
            "allZipMembersParsedAndCrcChecked": True,
            "nativeBudgetFormulaTextsVerified": True}
        manifest_path = directory / "complete-manifest.json"
        manifest_path.write_bytes(raw)
        if _file(manifest_path) != {key: descriptors[0, "json"][key]
                for key in ("bytes", "sha256")}:
            raise AssertionError("persisted manifest bytes differ from compact root")
        (directory / "scale-evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        return evidence
    except Exception as error:
        (directory / "failure.json").write_text(json.dumps({
            "schemaVersion": "business-budget-v10-static-scale-failure-v1",
            "syntheticOnly": True, "errorType": type(error).__name__,
            "error": str(error), "elapsedSeconds": round(time.monotonic() - started, 3),
            "syntheticRowsConsumed": source.consumed if "source" in locals() else 0,
            "peakTemporaryOutputBytes": peak_temp_bytes},
            ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    finally:
        tracemalloc.stop()


def _file_bytes(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument("--max-rows", type=int, default=1_000_000)
    parser.add_argument("--suffix-bytes", type=int, default=68)
    parser.add_argument("--deadline-seconds", type=int, default=1800)
    parser.add_argument("--slim-html-v10", action="store_true")
    parser.add_argument("--renderer-version", type=int, choices=(10, 11), default=10)
    arguments = parser.parse_args()
    print(json.dumps(run(arguments.output_dir, rows=arguments.rows,
        max_rows=arguments.max_rows, suffix_bytes=arguments.suffix_bytes,
        deadline_seconds=arguments.deadline_seconds,
        html_slim_v10=arguments.slim_html_v10,
        renderer_version=arguments.renderer_version), ensure_ascii=False))

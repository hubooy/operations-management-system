"""Default-closed, unregistered sealed-v2 report/file preview.

The owning report and every selected source are checked before and after the
full replay.  Files live in memory only; this module never creates a file row,
Agent receipt, workflow node or download authorization.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
import xml.etree.ElementTree as ET

from business_analysis import (cross_source_category_spu_compare as category_spu,
    cross_source_kpi_plan as planning, cross_source_sku_window_compare as sku_compare,
    cross_source_window_compare as shop_compare, finance_b2b_source_proof,
    report_composition_tables_v1 as table_projection,
    report_composition_v1 as composition)
from business_analysis.contracts import AnalysisContractError
from business_analysis.partitioned import Checkpoint
from business_analysis.report_files import write_pair

from . import business_cross_source_daily_materials as daily_owner
from . import business_cross_source_sku_materials as sku_owner
from . import business_erp_rollup_materials as erp_owner
from .business_sealed import Reader
from .policy import AiError, canonical, digest


SCHEMA = "business-report-composition-owning-preview-v1"
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _need(ok, message="报告组合来源、权限或写入回执已变化"):
    if not ok:
        raise AiError(message, "conflict", 409)


def _snapshot(report_id, principal, selected):
    _, snapshot, evidence, sources, fixed = erp_owner._bound(report_id, principal)
    reader = Reader(evidence, principal)
    infos = {key: reader.info(key) for key in selected}
    return snapshot, evidence, sources, fixed, infos


def _category_result(report_id, keys, principal, plan, sources, infos,
                     context, checkpoint, erp_scratch_bytes):
    """Use the owning rollup lifetime for the two extra ERP grains."""
    erp_materials, native_pages = {}, {}
    reader = Reader(erp_owner._bound(report_id, principal)[2], principal)
    for window in planning.WINDOWS:
        sales_key = keys["erpSales"][window]
        pair = sku_owner._pair(plan, window)
        _need((sales_key is None) == (pair is None))
        if sales_key is None:
            erp_materials[window] = None
        else:
            with erp_owner.prepare(report_id, pair, sales_key, keys["master"],
                    principal, checkpoint=checkpoint,
                    assignment_scratch_bytes=erp_scratch_bytes) as prepared:
                manifest = prepared.manifest
                streams = {kind: list(prepared.ndjson_pages(kind))
                    for kind in category_spu.daily.erp_fact_rollups.KINDS}
                spu_key = keys["netshopSpu"][window]
                native_pages[window] = (list(reader.pages(spu_key,
                    checkpoint=checkpoint)) if spu_key else None)
                erp_materials[window] = (manifest, streams)
        if sales_key is None:
            spu_key = keys["netshopSpu"][window]
            native_pages[window] = (list(reader.pages(spu_key,
                checkpoint=checkpoint)) if spu_key else None)
    return category_spu.prepare_candidate(plan, sources, infos, context,
        keys, erp_materials, native_pages)


def _verify_pair(metadata, tables, receipt, xlsx_bytes, html_bytes):
    """Independently inspect both bytes and the writer's row proofs."""
    _need(len(receipt["tables"]) == len(tables) == 13)
    for table, expected, written in zip(tables, metadata["tableAudit"] +
            [{"rowCount": tables[-1].row_count,
              "rowDigest": metadata["auditTableDigest"]}], receipt["tables"]):
        _need((table.key, table.row_count) ==
            (written["key"], written["rowCount"])
            and written["rowCount"] == expected["rowCount"]
            and written["rowDigest"] == expected["rowDigest"])
    marker = '<script type="application/json" id="report-data">'
    html_text = html_bytes.decode("utf-8")
    _need(html_text.count(marker) == 1)
    payload = json.loads(html_text.split(marker, 1)[1].split("</script>", 1)[0])
    _need(len(payload["tables"]) == 13)
    for table, part, written in zip(tables, payload["tables"], receipt["tables"]):
        rows = part["rows"]
        _need(part["key"] == table.key and part["proof"] == written
            and len(rows) == table.row_count
            and hashlib.sha256("".join(canonical(row) + "\n" for row in rows)
                .encode("utf-8")).hexdigest() == written["rowDigest"])
    with zipfile.ZipFile(io.BytesIO(xlsx_bytes)) as archive:
        stored = json.loads(archive.read("teruisi-manifest.json"))
        _need(stored["tables"] == receipt["tables"])
        for index, table in enumerate(tables, 1):
            root = ET.fromstring(archive.read(
                f"xl/worksheets/sheet{index}.xml"))
            physical = root.findall(f".//{_NS}sheetData/{_NS}row")
            _need(len(physical) - 3 == table.row_count)


def prepare(report_id, source_keys, principal, *, enabled=False,
            include_category_spu=True, checkpoint=None,
            erp_scratch_bytes=None):
    """Return an in-memory paired preview after exact current-source fences.

    v2's 2,000 pages/64 MiB per selected source and this projection's
    50,000 rows/32 MiB remain hard limits; no truncation or v4 claim.
    """
    _need(enabled is True, "报告组合拥有方预览默认关闭")
    keys = daily_owner._selection(source_keys)
    check = Checkpoint.wrap(checkpoint)
    selected = sku_owner._selected(keys)
    if check is not None:
        check({"stage": "report_composition_owning", "phase": "before"})
    snapshot, evidence, sources, fixed, infos = _snapshot(
        report_id, principal, selected)
    context = {"reportId": fixed["reportId"], "evidenceRunId": evidence.id,
        "evidenceVersion": evidence.version,
        "sealedDigest": snapshot["sealedDigest"],
        "ownerEmail": principal.email.lower(), "scope": principal.scope}
    try:
        plan = planning.prepare_candidate(sources, infos, context, keys)
        _need(plan["mappingPlan"] == snapshot["mappingPlan"]
            and plan["mappingPlanDigest"] == snapshot["mappingPlanDigest"])
        # The independent SKU owner checks all three selected daily replays.
        sku_owned = sku_owner.prepare(report_id, keys, principal,
            enabled=True, checkpoint=check,
            erp_scratch_bytes=erp_scratch_bytes)
        _need(sku_owned["binding"]["reportBinding"] == fixed
            and sku_owned["binding"]["planDigest"] == plan["planDigest"])
        materials = {}
        for window in planning.WINDOWS:
            result = daily_owner.prepare(report_id, keys,
                sku_owner._pair(plan, window), window, principal,
                checkpoint=check, erp_scratch_bytes=erp_scratch_bytes)
            _need(result["reportBinding"] == fixed
                and result["planDigest"] == plan["planDigest"]
                and result["resultDigest"] == digest({k: v for k, v in
                    result.items() if k != "resultDigest"}))
            materials[window] = result["material"]
        _need(sku_owned["binding"]["materialDigests"] == {
            window: materials[window]["materialDigest"]
            for window in planning.WINDOWS})
        category = (_category_result(report_id, keys, principal, plan,
            sources, infos, context, check, erp_scratch_bytes)
            if include_category_spu else None)
        shop = shop_compare.prepare_candidate(plan, sources, infos,
            context, keys, materials)
        sku = sku_compare.prepare_candidate(plan, sources, infos,
            context, keys, materials)
        _need(sku["comparisonDigest"] ==
            sku_owned["comparison"]["comparisonDigest"])
        finance_context = finance_b2b_source_proof.build_candidate()
        binding = {"schemaVersion": composition.PROOF_SCHEMA,
            "reportId": plan["reportId"], "planDigest": plan["planDigest"],
            "evidenceRunId": context["evidenceRunId"],
            "sealedDigest": context["sealedDigest"],
            "materialDigests": {window: materials[window]["materialDigest"]
                for window in planning.WINDOWS},
            "componentDigests": {"store": shop["comparisonDigest"],
                "sku": sku["comparisonDigest"],
                "categorySpu": category["comparisonDigest"] if category else None,
                "keyword": None,
                "financeB2b": finance_context["candidateDigest"],
                "market": None}, "externalContextSameReportClaimed": False}
        proof = {**binding, "bindingDigest": digest(binding)}
        def verify(supplied, expected):
            if supplied != proof or expected != binding:
                return False
            latest = _snapshot(report_id, principal, selected)
            return (latest[3] == fixed and latest[4] == infos
                and latest[1].id == evidence.id
                and latest[1].version == evidence.version)
        metadata, tables = table_projection.prepare(plan, sources, infos,
            context, keys, materials, category_spu_result=category,
            owning_proof=proof, verify_owning_proof=verify)
        xlsx, html = io.BytesIO(), io.BytesIO()
        receipt = write_pair(xlsx, html, title="经营分析三期同源核对预览",
            metadata=metadata, tables=tables, checkpoint=check,
            xlsx_opc_version=2)
        xlsx_bytes, html_bytes = xlsx.getvalue(), html.getvalue()
        _verify_pair(metadata, tables, receipt, xlsx_bytes, html_bytes)
        if check is not None:
            check({"stage": "report_composition_owning", "phase": "complete"})
        _need(verify(proof, binding), "文件生成后报告、账号或来源修订变化")
        body = {"schemaVersion": SCHEMA, "reportBinding": fixed,
            "planDigest": plan["planDigest"],
            "sourceKeys": keys,
            "sourceEvidenceDigests": {key:
                infos[key]["expected"]["evidenceDigest"] for key in selected},
            "sourceRevisions": {key:
                infos[key]["metadata"]["sourceRevision"] for key in selected},
            "promotionSourceKeys": sorted({key for key in
                keys["promotion"].values() if key is not None}),
            "compositionDigest": metadata["compositionDigest"],
            "deliveryDigest": metadata["deliveryDigest"],
            "tableAudit": metadata["tableAudit"],
            "pairReceiptDigest": digest(receipt),
            "htmlSha256": hashlib.sha256(html_bytes).hexdigest(),
            "xlsxSha256": hashlib.sha256(xlsx_bytes).hexdigest(),
            "tableCount": len(tables), "pairedBytesVerified": True,
            "categorySpuPresent": category is not None,
            "financeB2bMarketContextOnly": True,
            "registeredRenderer": False, "agentReadPersisted": False,
            "authorityVerified": False, "published": False}
        return {"manifest": {**body, "resultDigest": digest(body)},
            "tables": tables, "pairReceipt": receipt,
            "html": html_bytes, "xlsx": xlsx_bytes}
    except (AnalysisContractError, KeyError, TypeError, ValueError,
            UnicodeError, OverflowError, zipfile.BadZipFile,
            ET.ParseError) as error:
        raise AiError("综合报告未通过封存来源及HTML/XLSX逐表核对",
            "conflict", 409) from error

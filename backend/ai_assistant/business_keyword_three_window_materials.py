"""Default-closed, unregistered three-window JD keyword material.

This replays the selected sealed-v2 promotion sources through the existing
owning keyword reader. It is a bounded candidate, not a report or Agent read.
"""
from __future__ import annotations

import hashlib

from business_analysis import cross_source_kpi_plan as planning
from business_analysis.contracts import AnalysisContractError
from business_analysis.partitioned import Checkpoint

from . import business_cross_source_daily_materials as daily_owner
from . import business_diagnostic_screening as report_binding
from . import business_promotion_keyword_sku as keyword_owner
from .policy import AiError, canonical, digest


SCHEMA = "business-keyword-three-window-owning-candidate-v1"
MAX_ROWS = 50_000
MAX_RESULT_BYTES = 32 * 1024 * 1024


def _need(ok, message="关键词三期来源、完整行或当前报告已变化"):
    if not ok:
        raise AiError(message, "conflict", 409)


def _read(report_id, current_key, baseline_key, principal, fixed, check):
    with keyword_owner.table(report_id, current_key, "keyword_sku", principal,
            baseline_key=baseline_key, checkpoint=check) as (opened, binding):
        _need(binding["reportBinding"] == fixed
            and binding["sourceKey"] == current_key
            and binding["baselineKey"] == baseline_key
            and binding["view"] == "keyword_sku")
        header = opened.header()
        _need(header["authorityVerified"] is False
            and header["source"]["key"] == current_key
            and (header["baselineSource"] or {}).get("key") == baseline_key)
        rows = {}
        for index, row in enumerate(opened.scan()):
            _need(index < MAX_ROWS and row["rowIndex"] == index)
            identity = canonical(row["entity"])
            _need(identity not in rows)
            rows[identity] = row
        _need(len(rows) == header["total"])
    return header, rows, digest({"header": header,
        "rows": [rows[key] for key in sorted(rows)]})


def _cell(row, *, baseline=False, missing_source=False):
    if missing_source:
        return {"status": "missing_source", "rowCount": None,
            "metrics": None, "ratios": None}
    if row is None:
        return {"status": "not_observed", "rowCount": None,
            "metrics": None, "ratios": None}
    return {"status": "observed", "rowCount": row[
        "baselineRowCount" if baseline else "currentRowCount"],
        "metrics": row["baselineMetrics" if baseline else "metrics"],
        "ratios": None if baseline else row["ratios"]}


def _combine(current_header, current_rows, pair_results, keys):
    """Merge two full comparisons by raw entity, never by pair-specific ID."""
    identities = set(current_rows)
    for header, rows, _ in pair_results.values():
        _need(header["sourceMetadata"] == current_header["sourceMetadata"]
            and header["sourceQueryDigest"] == current_header["sourceQueryDigest"]
            and header["periods"] == current_header["periods"]
            and set(current_rows) <= set(rows),
            "关键词两次比较的本期来源、覆盖或分组不一致")
        identities.update(rows)
        for identity, original in current_rows.items():
            pair = rows[identity]
            _need(all(pair[field] == original[field] for field in (
                "entity", "currentRowCount", "metrics", "ratios",
                "identityQualified", "missingIdentityFields")),
                "关键词两次比较的本期事实已变化")
    output = []
    for identity in sorted(identities):
        current = current_rows.get(identity)
        pairs = {window: pair_results[window][1].get(identity)
            if window in pair_results else None
            for window in planning.WINDOWS[1:]}
        example = current or next(row for row in pairs.values() if row)
        output.append({"entity": example["entity"],
            "identityQualified": example["identityQualified"],
            "missingIdentityFields": example["missingIdentityFields"],
            "windows": {"current": _cell(current), **{
                window: _cell(pairs[window], baseline=True,
                    missing_source=keys[window] is None)
                for window in planning.WINDOWS[1:]}},
            "comparisons": {window: pairs[window]["comparisons"]
                if pairs[window] is not None else None
                for window in planning.WINDOWS[1:]}})
        _need(len(output) <= MAX_ROWS, "关键词三期实体超过候选容量")
    return output


def prepare(report_id, source_keys, principal, *, enabled=False,
            checkpoint=None):
    """Return full, bounded candidate rows; never register a file or tool."""
    _need(enabled is True, "关键词三期拥有方候选默认关闭")
    keys = daily_owner._selection(source_keys)
    check = Checkpoint.wrap(checkpoint)
    fixed, _, _, mapping, sources, infos = report_binding._load(
        report_id, principal)
    context = {"reportId": fixed["reportId"],
        "evidenceRunId": fixed["evidenceRunId"],
        "evidenceVersion": fixed["evidenceVersion"],
        "sealedDigest": fixed["sealedDigest"],
        "ownerEmail": principal.email.lower(), "scope": principal.scope}
    try:
        plan = planning.prepare_candidate(sources, infos, context, keys)
        _need(plan["mappingPlan"] == mapping
            and plan["mappingPlanDigest"] == fixed["mappingPlanDigest"])
        promotion = keys["promotion"]
        current_key = promotion["current"]
        current_header, current_rows, current_digest = _read(
            report_id, current_key, None, principal, fixed, check)
        _need(current_header["sourceWindow"] == "current"
            and current_header["periods"] == plan["periods"])
        pairs = {}
        for window in planning.WINDOWS[1:]:
            source_key = promotion[window]
            if source_key is None:
                continue
            header, rows, table_digest = _read(report_id, current_key,
                source_key, principal, fixed, check)
            _need(header["comparisonWindow"] == window
                and header["baselineSource"]["key"] == source_key
                and header["periods"] == plan["periods"])
            pairs[window] = (header, rows, table_digest)
        rows = _combine(current_header, current_rows, pairs, promotion)
        if check is not None:
            check({"stage": "keyword_three_window", "phase": "complete"})
        report_binding._revalidate(fixed, principal)
        selected = sorted(set(key for key in promotion.values()
            if key is not None))
        row_hash = hashlib.sha256()
        for row in rows:
            row_hash.update((canonical(row) + "\n").encode("utf-8"))
        body = {"schemaVersion": SCHEMA, "reportBinding": fixed,
            "planDigest": plan["planDigest"], "sourceKeys": promotion,
            "sourceEvidenceDigests": {key: infos[key]["expected"][
                "evidenceDigest"] for key in selected},
            "sourceRevisions": {key: infos[key]["metadata"][
                "sourceRevision"] for key in selected},
            "periods": plan["periods"],
            "tableDigests": {"current": current_digest, **{
                window: pairs[window][2] if window in pairs else None
                for window in planning.WINDOWS[1:]}},
            "rows": rows, "rowCount": len(rows),
            "rowDigest": row_hash.hexdigest(),
            "completeSealedV2SourcesReplayed": True,
            "shopUniqueVisitorsAvailable": False,
            "historicalErpOwnershipVerified": False,
            "crossDomainAmountsAdded": False,
            "agentReadPersisted": False, "registeredRenderer": False,
            "authorityVerified": False, "published": False}
        _need(len(canonical(body).encode("utf-8")) <= MAX_RESULT_BYTES,
            "关键词三期完整候选超过固定容量")
        return {**body, "resultDigest": digest(body)}
    except (AnalysisContractError, KeyError, TypeError, ValueError,
            UnicodeError, OverflowError) as error:
        raise AiError("关键词三期候选未通过来源、覆盖和完整行核对",
            "conflict", 409) from error

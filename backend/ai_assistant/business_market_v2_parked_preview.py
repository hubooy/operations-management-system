"""Signed, read-only preview of an exact parked market-v2 report.

This is a human inspection surface, not an Agent tool or material admission.
The owning source and all three typed tables are replayed on every request.
"""
from __future__ import annotations

from itertools import islice
import json

from . import business_diagnostic_screening as screening
from . import business_market_report_material as material_owner
from . import business_market_v2_parked_creation as parked
from . import models as m
from .policy import AiError, canonical, current_principal, digest, fields, identifier


SCHEMA = "business-market-v2-parked-preview-v1"
MAX_RESPONSE_BYTES = 38_000
VIEWS = ("price_band_summary", "price_band_members", "rank_entry_exit")


def _need(ok, message="市场样本预览与当前停放报告不一致"):
    if not ok:
        raise AiError(message, "conflict", 409)


def _parked(report_id, principal):
    actor = current_principal(principal, admin=True)
    row = m.AiReportRun.objects.select_related("workflow").filter(pk=report_id).first()
    if row is None or row.owner_email != actor.email.lower():
        raise AiError("报告不存在或不在当前授权范围", "not_found", 404)
    snapshot, flow = json.loads(row.snapshot_json), row.workflow
    workflow_input = json.loads(flow.input_json)
    _need(row.scope_json == "null" and flow.scope_json == "null"
        and flow.owner_email == actor.email.lower()
        and snapshot.get("schemaVersion") == parked.SNAPSHOT_SCHEMA
        and snapshot.get("executionProfile") == parked.runtime.PROFILE
        and snapshot.get("reportId") == report_id
        and snapshot.get("evidenceProtocol") == "reference-v2"
        and snapshot.get("marketMaterialReady") is False
        and snapshot.get("registered") is False
        and workflow_input.get("schemaVersion") == parked.INPUT_SCHEMA
        and workflow_input.get("reportId") == report_id
        and workflow_input.get("marketSelector") == snapshot.get("marketSelector")
        and workflow_input.get("sourceRoot") == snapshot.get("sourceRoot")
        and workflow_input.get("allowedTools") == []
        and row.snapshot_json == canonical(snapshot)
        and flow.input_json == canonical(workflow_input)
        and flow.graph_digest == digest(flow.graph_json)
        and flow.tool_policy_digest == parked.EMPTY_TOOL_POLICY_DIGEST
        and flow.status == "paused" and flow.error_code == parked.PAUSE_REASON
        and flow.retryable == 0 and flow.dry_run == 0
        and flow.allowed_tools_json == "[]"
        and flow.model_id == "" and flow.model_version == 0
        and flow.provider_round_count == 0 and flow.tool_call_count == 0
        and not m.AiWorkflowNodeRuns.objects.filter(run_id=flow.id).exists()
        and not m.AiAgentJobs.objects.filter(workflow_run_id=flow.id).exists()
        and not m.AiBusinessFileRun.objects.filter(report_id=report_id).exists(),
        "市场报告已非原始停放状态")
    root = snapshot["sourceRoot"]
    fixed = screening._load(root["sourceReportId"], principal)[0]
    _need(fixed["workflowId"] == root["sourceWorkflowId"]
        and fixed["snapshotDigest"] == root["sourceSnapshotDigest"]
        and fixed["workflowInputDigest"] == root["sourceWorkflowInputDigest"]
        and fixed["evidenceRunId"] == root["evidenceRunId"]
        and fixed["evidenceVersion"] == root["evidenceVersion"]
        and fixed["sealedDigest"] == root["sealedDigest"],
        "市场来源封存根已变化")
    return digest({"snapshot": row.snapshot_json,
        "workflowInput": flow.input_json, "workflowStatus": flow.status,
        "sourceRoot": root, "sourceBinding": fixed})


def _selection(params):
    fields(params, {"view", "offset", "limit"})
    view = params.get("view")
    if view is None:
        if "offset" in params or "limit" in params:
            raise AiError("摘要请求不能携带分页参数")
        return None, 0
    if view not in VIEWS:
        raise AiError("市场样本视图无效")
    offset = params.get("offset", "0")
    if (not isinstance(offset, str) or not offset.isascii()
            or not offset.isdecimal() or len(offset) > 6
            or str(int(offset)) != offset or int(offset) > 200_000
            or params.get("limit", "20") != "20"):
        raise AiError("市场样本固定分页无效")
    return view, int(offset)


def read(report_id, params, principal):
    """Return at most 20 projected rows after a full owning replay."""
    report_id = identifier(report_id, "reportId")
    view, offset = _selection(params)
    before = _parked(report_id, principal)
    row = m.AiReportRun.objects.get(pk=report_id)
    snapshot = json.loads(row.snapshot_json)
    source_id, selector = (snapshot["sourceRoot"]["sourceReportId"],
        snapshot["marketSelector"])
    with material_owner.prepare(source_id, selector, principal) as prepared:
        manifest, summary, tables = (prepared.manifest, prepared.summary,
            prepared.tables)
        _need(summary["selectedSealedSourcesFullyReplayed"] is True
            and summary["typedMarketRowsVerified"] is True
            and summary["authorityVerified"] is False
            and summary["reportId"] == source_id
            and manifest["algorithms"] == snapshot["marketAlgorithms"]
            and [table.key for table in tables] == ["market-v2-"+name
                for name in VIEWS])
        table_info = [{"view": name, "title": table.title,
            "rowCount": table.row_count,
            "columns": [{"key": col.key, "label": col.label,
                "kind": col.kind} for col in table.columns]}
            for name, table in zip(VIEWS, tables)]
        page = None
        if view is not None:
            table = tables[VIEWS.index(view)]
            page = {"view": view, "offset": offset, "limit": 20,
                "total": table.row_count,
                "columns": table_info[VIEWS.index(view)]["columns"],
                "rows": list(islice(table.rows, offset, offset+20))}
        material_digest = manifest["manifestDigest"]
        coverage = manifest["rankObservationCoverage"]
    _need(_parked(report_id, principal) == before,
        "市场停放报告或来源在预览期间变化")
    result = {"schemaVersion": SCHEMA, "reportId": report_id,
        "sourceReportId": source_id, "workflowStatus": "paused",
        "pauseReason": parked.PAUSE_REASON,
        "marketManifestDigest": material_digest,
        "observationCoverage": coverage, "tables": table_info,
        "page": page,
        "materialAdmitted": False, "agentReadPersisted": False,
        "actualAgentBound": False, "authorityVerified": False,
        "renderer": False, "requestMaterialReplayed": True,
        "marketTopSampleOnly": True,
        "priceSummaryAndMembersAdditive": False,
        "marketAndOwnSalesAdditive": False}
    result["resultDigest"] = digest(result)
    if len(canonical(result).encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise AiError("市场样本预览超过固定响应容量", "payload_too_large", 413)
    return result

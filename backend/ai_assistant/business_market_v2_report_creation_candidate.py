"""Default-closed market-v2 report creation proposal over one sealed v2 root.

This only prepares reviewable snapshot/workflow bytes. It never creates a
report, registers a profile/tool, dispatches an Agent or produces a file.
"""
from __future__ import annotations

import json

from business_analysis.contracts import AnalysisContractError

from . import business_diagnostic_screening as screening
from . import business_market_report_material as material_owner
from . import business_promotion_market_admission as admission
from . import business_promotion_market_runtime_v2_contract as runtime_contract
from . import business_promotion_runtime_contract as promotion_contract
from . import models as m
from .policy import AiError, canonical, digest, identifier


SCHEMA = "business-market-v2-report-creation-candidate-v1"
SNAPSHOT_SCHEMA = "business-market-v2-snapshot-candidate-v1"
INPUT_SCHEMA = "business-market-v2-workflow-input-candidate-v1"
MAX_CANDIDATE_BYTES = 256 * 1024


def _need(ok, message="市场v2报告候选与原词货筛查或封存材料不一致"):
    if not ok:
        raise AiError(message, "conflict", 409)


def prepare(source_report_id, target_report_id, selector, principal, *,
            limits=None, checkpoint=None):
    """Return a deterministic candidate; target ID remains unallocated."""
    source_report_id = identifier(source_report_id, "sourceReportId")
    target_report_id = identifier(target_report_id, "targetReportId")
    _need(source_report_id != target_report_id
        and not m.AiReportRun.objects.filter(pk=target_report_id).exists(),
        "市场v2候选目标报告ID已被占用")
    fixed, _, _, _, _, _ = screening._load(source_report_id, principal)
    _need(fixed["executionProfile"] == promotion_contract.PROFILE
        and fixed["scope"] is None,
        "市场v2候选仅承接当前无范围管理员的v1词货筛查报告")
    base = m.AiReportRun.objects.select_related("workflow").get(pk=source_report_id)
    _need(base.workflow_id == fixed["workflowId"]
        and digest(base.snapshot_json) == fixed["snapshotDigest"]
        and digest(base.workflow.input_json) == fixed["workflowInputDigest"])
    base_snapshot, base_input = json.loads(base.snapshot_json), json.loads(
        base.workflow.input_json)
    _need(base_snapshot["executionProfile"] == promotion_contract.PROFILE
        and base_snapshot["evidenceProtocol"] == "reference-v2"
        and base_snapshot["reportId"] == source_report_id
        and base_snapshot["screeningIntent"] == base_input["screeningIntent"]
        and bool(base.budget_plan_id) == ("budgetRef" in base_snapshot),
        "词货v1筛查意图、预算或证据协议变化")
    selected = admission.require_observed(source_report_id, selector,
        principal, checkpoint=checkpoint)
    _need(selected["binding"]["reportBindingDigest"] == digest(fixed)
        and selected["binding"]["reportId"] == source_report_id)
    runtime = runtime_contract.prepare(selected,
        with_budget=bool(base.budget_plan_id))
    try:
        with material_owner.prepare(source_report_id, runtime["marketSelector"],
                principal, checkpoint=checkpoint, limits=limits) as prepared:
            manifest, material = prepared.manifest, prepared.summary
            _need(material["admissionDigest"] == selected["bindingDigest"]
                and material["marketManifestDigest"] == manifest["manifestDigest"]
                and material["typedMarketRowsVerified"] is True
                and material["selectedSealedSourcesFullyReplayed"] is True
                and material["authorityVerified"] is False)
    except (AnalysisContractError, KeyError, ValueError, TypeError,
            UnicodeError, OverflowError, RecursionError) as error:
        raise AiError("市场v2报告候选未通过拥有方完整三表材料",
            "conflict", 409) from error
    root = {"sourceReportId": source_report_id,
        "sourceWorkflowId": fixed["workflowId"],
        "sourceReportBindingDigest": digest(fixed),
        "sourceSnapshotDigest": fixed["snapshotDigest"],
        "sourceWorkflowInputDigest": fixed["workflowInputDigest"],
        "evidenceRunId": fixed["evidenceRunId"],
        "evidenceVersion": fixed["evidenceVersion"],
        "sealedDigest": fixed["sealedDigest"],
        "screeningIntent": base_snapshot["screeningIntent"],
        "budgetRef": base_snapshot.get("budgetRef")}
    market = {"selector": runtime["marketSelector"],
        "admissionDigest": selected["bindingDigest"],
        "marketContextDigest": runtime["marketContextDigest"],
        "manifestDigest": manifest["manifestDigest"],
        "sourceDescriptorsDigest": manifest["sourceDescriptorsDigest"],
        "observationDates": manifest["observationDates"],
        "observationCoverage": manifest["rankObservationCoverage"],
        "materialTables": [{key: spec[key] for key in
            ("view", "rowCount", "pageCount", "ndjsonBytes", "ndjsonSha256",
             "sourceTableDigest", "bindingDigest")}
            for spec in manifest["tables"]],
        "marketTopSampleOnly": True,
        "wholeMarketCoverageVerified": False,
        "ownSalesAttributionVerified": False}
    snapshot = {"schemaVersion": SNAPSHOT_SCHEMA,
        "executionMode": "parallel-v1-candidate-only",
        "executionProfile": runtime_contract.PROFILE,
        "evidenceProtocol": "reference-v2",
        "reportId": target_report_id, "sourceRoot": root,
        "market": market,
        "question": base_snapshot["question"],
        "scope": base_snapshot["scope"],
        "libraryVersion": base_snapshot["libraryVersion"],
        "template": base_snapshot["template"],
        "skills": base_snapshot["skills"],
        "registered": False}
    workflow_input = {"schemaVersion": INPUT_SCHEMA,
        "executionProfile": runtime_contract.PROFILE,
        "reportId": target_report_id,
        "sourceRoot": root,
        "sourceWorkflowInputDigest": digest(base_input),
        "market": market,
        "graphDigest": runtime["graphDigest"],
        "allowedTools": runtime["allowedTools"],
        "roleReadPolicy": runtime["roleReadPolicy"],
        "humanReviewRequired": True,
        "registered": False}
    result = {"schemaVersion": SCHEMA,
        "targetReportId": target_report_id,
        "sourceReportId": source_report_id,
        "snapshot": snapshot,
        "snapshotDigest": digest(snapshot),
        "workflowInput": workflow_input,
        "workflowInputDigest": digest(workflow_input),
        "workflowGraph": runtime["graph"],
        "workflowGraphDigest": runtime["graphDigest"],
        "allowedTools": runtime["allowedTools"],
        "roleReadPolicy": runtime["roleReadPolicy"],
        "marketMaterialManifestDigest": manifest["manifestDigest"],
        "marketMaterialRowsVerified": True,
        "marketSourceCapacity": {"rowCount": manifest["rowCount"],
            "pageCount": manifest["pageCount"],
            "ndjsonBytes": manifest["ndjsonBytes"],
            "maxRows": material_owner.composite.previous.MAX_ROWS,
            "maxBytes": material_owner.composite.previous.MAX_BYTES},
        "marketTopSampleOnly": True,
        "marketAndOwnSalesAdditive": False,
        "agentDispatchSupported": False,
        "toolRegistered": False,
        "reportCreateSupported": False,
        "renderer8Supported": False,
        "humanReviewRequired": True,
        "registered": False}
    result["candidateDigest"] = digest(result)
    _need(len(canonical(result).encode("utf-8")) <= MAX_CANDIDATE_BYTES,
        "市场v2新报告候选超过固定容量")
    _need(admission.require_observed(source_report_id,
        runtime["marketSelector"], principal)["bindingDigest"] ==
        selected["bindingDigest"]
        and digest(screening._revalidate(fixed, principal)[0]) == digest(fixed)
        and not m.AiReportRun.objects.filter(pk=target_report_id).exists(),
        "市场v2候选返回前报告、账号或目标ID变化")
    return result

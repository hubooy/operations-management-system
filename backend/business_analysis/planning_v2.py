"""Explicit v2 scope preview; no source reads, collection or model admission.

The legacy planner remains byte-for-byte unchanged. Its bounded, exact scope
expansion is reused with internal proposal limits; v2 admission is rebuilt from
the catalog contract, never inherited from the legacy source-bearing input.
"""
from copy import deepcopy

from . import evidence_v2, planning
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical


REQUEST_SCHEMA = "business-plan-request-v2"
PREVIEW_SCHEMA = "business-plan-preview-v2"
# Four shops: four facts * three windows + current master + ten ERP channels *
# three windows; seven market conditions * three windows. This is proposal
# enumeration only. Admission still allows at most 48 supported sources.
MAX_PROPOSAL_SOURCES = 4 * (4 * 3 + 1 + 10 * 3) + 7 * 3
MAX_PROPOSAL_BYTES = 1024 * 1024
LEGACY_ESTIMATE_NOTE = "workflowBytes按当前45字符evidence-UUID及无预算DAG输入测算，后端创建时仍须最终核验。"


def preview(body, *, netshop_sources=None, market_validator=None):
    """Preserve the complete proposed scope even when admission is refused.

    capacity.queryBytes is the maximum single canonical UTF-8 query size;
    directoryQueryBytes is the sum of canonical query sizes. planBytes and
    workflowBytes are null when no complete valid v2 plan can be constructed.
    Placeholder workflow input is only a representation upper-bound estimate,
    not a seal, model-context admission or promise of available business facts.
    """
    if type(body) is not dict or body.get("schemaVersion") != REQUEST_SCHEMA:
        raise AnalysisContractError("分析计划请求版本无效")
    proposal = planning.preview({key: value for key, value in body.items() if key != "schemaVersion"},
        max_sources=MAX_PROPOSAL_SOURCES, max_plan_bytes=MAX_PROPOSAL_BYTES, max_workflow_bytes=MAX_PROPOSAL_BYTES,
        netshop_sources=netshop_sources, market_validator=market_validator)
    sources = proposal["sources"]
    analysis = proposal["evidenceRequest"]["analysisRequest"]
    query_sizes = [len(canonical(source["query"]).encode("utf-8")) for source in sources]
    capacity = {"sourceCount": len(sources), "maxSources": evidence_v2.MAX_SOURCES,
        "planBytes": None, "maxPlanBytes": evidence_v2.MAX_HEADER_BYTES,
        "workflowBytes": None, "maxWorkflowBytes": evidence_v2.MAX_WORKFLOW_BYTES,
        "queryBytes": max(query_sizes, default=0), "maxQueryBytes": evidence_v2.MAX_QUERY_BYTES,
        "directoryQueryBytes": sum(query_sizes), "maxDirectoryQueryBytes": evidence_v2.MAX_DIRECTORY_QUERY_BYTES,
        "factBytes": 64 * 1024 * 1024, "factPages": 2000}
    limitations = [note for note in proposal["limitations"] if note != LEGACY_ESTIMATE_NOTE]
    limitations.extend([
        "v2最多48个来源只是目录容量；事实仍限64MiB/2000总页；目录元数据另有独立边界，且计入用户及全局额度。",
        "queryBytes为最大单来源规范查询UTF-8字节，directoryQueryBytes为全部来源查询字节之和。",
        "workflowBytes仅按固定占位证据ID、最大安全整数版本和占位封存摘要估算轻量输入上界；不是真实封存或模型上下文预检。",
        "正式分析仍须在证据封存后手动启动，并通过实际模型的完整目录、工具调用及上下文预检；当前不支持v2固定预算。",
    ])
    blockers = []
    for measured, bound, label in (("sourceCount", "maxSources", "来源数量"), ("queryBytes", "maxQueryBytes", "单来源查询UTF-8字节"),
                                    ("directoryQueryBytes", "maxDirectoryQueryBytes", "目录查询总UTF-8字节")):
        if capacity[measured] > capacity[bound]:
            blockers.append(f"{label}{capacity[measured]}超过上限{capacity[bound]}；完整请求保留，不自动缩小或拆分后冒充综合报告。")
    built, workflow = None, None
    # Unsupported proposals must not acquire a valid header for only their
    # remaining supported subset. Keep every requested condition in coverage.
    if proposal["canCollect"] and not blockers:
        try:
            candidate = evidence_v2.build_catalog(sources, analysis_request=analysis)
            reference = evidence_v2.workflow_reference(sources, run_id=planning.EVIDENCE_ID_PLACEHOLDER,
                evidence_version=MAX_SAFE_INTEGER, sealed_digest="0" * 64,
                question=proposal["request"]["question"], analysis_request=analysis)
            built, workflow = candidate, reference
            capacity["planBytes"] = len(canonical(built["header"]).encode("utf-8"))
            capacity["workflowBytes"] = len(canonical(workflow).encode("utf-8"))
        except AnalysisContractError as error:
            blockers.append(str(error)+"；完整请求保留，尚不能构造完整v2计划。")
    if built is None:
        limitations.append("小计划与轻量输入容量尚不可测算，显示为null；这不表示零字节或已通过创建校验。")
    return {"schemaVersion": PREVIEW_SCHEMA, "request": {"schemaVersion": REQUEST_SCHEMA, **proposal["request"]},
        "capacityProfile": evidence_v2.CAPACITY_PROFILE,
        "planDigest": built["planDigest"] if built else None,
        "catalogDigest": built["header"]["catalogDigest"] if built else None,
        "plan": built["header"] if built else None,
        "workflowInputEstimate": workflow,
        "workflowEstimateAssumptions": {"evidenceRunId": planning.EVIDENCE_ID_PLACEHOLDER,
            "evidenceVersion": MAX_SAFE_INTEGER, "sealedDigest": "0" * 64, "modelAdmissionChecked": False},
        "sources": sources, "coverage": proposal["coverage"], "limitations": limitations + blockers,
        "canCollect": bool(built is not None and proposal["canCollect"] and not blockers), "capacity": capacity,
        "evidenceRequest": {"schemaVersion": evidence_v2.HEADER_SCHEMA, **deepcopy(proposal["evidenceRequest"])}}

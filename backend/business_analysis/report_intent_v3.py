"""Pure inert five-role workflow intent; never a runnable AI workflow graph."""
from __future__ import annotations

from . import report_reference_v3 as reference
from .contracts import AnalysisContractError, canonical, digest

INPUT_SCHEMA = "business-v3-paused-workflow-input-v1"
PLAN_SCHEMA = "business-v3-paused-workflow-plan-v1"
PAUSE_REASON = "v3_agents_not_registered"
MAX_BYTES = 131_072


def stage(candidate):
    if (type(candidate) is not dict or candidate.get("schemaVersion") != reference.SCHEMA
            or candidate.get("executionProfile") != reference.PROFILE
            or candidate.get("candidateDigest") != digest({k: v for k, v in candidate.items()
                if k != "candidateDigest"})
            or candidate.get("modelDispatchSupported") is not False
            or candidate.get("reportGenerationSupported") is not False
            or candidate.get("fileGenerationSupported") is not False):
        raise AnalysisContractError("v3持久意图只能从不可执行的固定候选生成")
    roles = list(reference.AGENTS)
    graph = {"schemaVersion": PLAN_SCHEMA, "executionProfile": reference.PROFILE,
        "status": "paused", "pauseReason": PAUSE_REASON,
        "agentDispatchRegistered": False, "modelAdmissionPerformed": False,
        "humanReviewRequired": True,
        "nodes": [{"key": key, "dependsOn": [] if key in roles[:3] else
            roles[:3] if key == "independent_review" else roles[:4],
            "status": "blocked", "dispatchRegistered": False} for key in roles] +
            [{"key": "human_review", "dependsOn": ["report"], "status": "blocked",
              "dispatchRegistered": False}]}
    input_value = {"schemaVersion": INPUT_SCHEMA, "executionProfile": reference.PROFILE,
        "pauseReason": PAUSE_REASON, "candidateDigest": candidate["candidateDigest"],
        "candidate": candidate, "reportGenerationSupported": False,
        "modelDispatchSupported": False, "fileGenerationSupported": False}
    if (len(canonical(graph).encode("utf-8")) > MAX_BYTES
            or len(canonical(input_value).encode("utf-8")) > MAX_BYTES):
        raise AnalysisContractError("v3暂停意图超出内部快照容量")
    return {"snapshot": candidate, "workflowInput": input_value, "workflowPlan": graph,
            "status": "paused", "pauseReason": PAUSE_REASON}

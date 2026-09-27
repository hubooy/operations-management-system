"""Test-only owning observation of one proposed v6 market tool read.

This executes the existing sealed-source reader twice under the current
account.  It does not create a job, provider/tool dispatch, read receipt, or
numeric citation.  The generated provider IDs are explicit non-persisted
observation labels, never a provider response.
"""
from django.conf import settings
from django.db import DatabaseError

from business_analysis.contracts import AnalysisContractError, digest
from business_analysis.promotion_views import _copy

from . import business_market_v2_execution_plan as plans
from . import business_market_v2_read_plan_v6 as v6_owner
from . import business_market_v2_tool_result_v6_contract as proof
from . import business_market_v2_transport_candidate as transport
from . import business_market_v2_transport_contract as transport_shape
from . import business_promotion_market_runtime_v2_contract as runtime
from .policy import AiError, current_principal, identifier


SCHEMA = "business-market-v2-owner-tool-observation-v6"


def _reject():
    raise AiError("市场 v6 拥有方观察来源或结果不可核验",
        "market_v2_tool_observation_unverified", 409)


def observe(source_execution_report_id, proposed_report_id,
        proposed_workflow_id, role, selection, principal):
    """Re-read a sealed source without provider traffic or persistent writes."""
    if (getattr(settings, "AI_MARKET_V2_TOOL_OBSERVATION_V6_ENABLED", False)
            is not True or settings.DJANGO_ENVIRONMENT != "test"):
        raise AiError("市场 v6 拥有方观察未启用",
            "market_v2_tool_observation_disabled", 409)
    source_id = identifier(source_execution_report_id, "sourceExecutionReportId")
    report_id = identifier(proposed_report_id, "proposedReportId")
    flow_id = identifier(proposed_workflow_id, "proposedWorkflowId")
    current_principal(principal, admin=True)
    if role not in runtime.MARKET_ROLES:
        _reject()
    selected = _copy(selection, 4096)
    if (type(selected) is not dict
            or "reportId" in selected or "marketContextDigest" in selected):
        _reject()
    try:
        planned = v6_owner.prepare(source_id, report_id, flow_id, principal)
        source = plans.read(source_id, principal)
        root = source["executionRoot"]
        slot = next(item for item in planned["snapshot"]["roleBindings"]
            if item["role"] == role)
        arguments = {"reportId": root["admittedReportId"],
            "marketContextDigest": root["marketContextDigest"], **selected}
        label = digest([planned["snapshotDigest"], role, arguments])
        call = {"schemaVersion":
            "business-market-v2-fifth-read-injected-call-v1",
            "admittedReportId": root["admittedReportId"],
            "jobId": slot["proposedJobId"],
            "providerDispatchId": "market-observation-provider-" + label[:48],
            "providerCallId": "observation-call-" + label[:48],
            "role": role, "marketManifestDigest": root["manifestDigest"],
            "marketContextDigest": root["marketContextDigest"]}

        def replay(fixed_call, fixed_arguments):
            return transport.read(transport_shape.SURFACE,
                transport_shape.PROFILE, transport_shape.TOOL, fixed_call,
                fixed_arguments, principal)

        observed = replay(call, arguments)
        checked = proof.check(planned, source, call, arguments, observed, replay)
        # The proposal and source are live reads, not locks.  Re-read after
        # both source traversals to reject concurrent authority changes.
        if (plans.read(source_id, principal) != source
                or v6_owner.prepare(source_id, report_id, flow_id,
                    principal)["snapshotDigest"] != planned["snapshotDigest"]):
            _reject()
        payload = observed["payload"]
        row_count = (len(payload.get("rows", [])) if selected["mode"] == "page"
            else 1 if selected["mode"] == "row" else None)
        value = {"schemaVersion": SCHEMA,
            "newReportId": report_id, "newWorkflowId": flow_id,
            "sourceExecutionReportId": source_id,
            "admittedReportId": root["admittedReportId"],
            "role": role, "mode": selected["mode"],
            "proposedJobId": slot["proposedJobId"],
            "argumentsDigest": checked["argumentsDigest"],
            "toolResultDigest": checked["toolResultDigest"],
            "observedRowCount": row_count,
            "sourceReplayedByOwner": True,
            "sameResultOnSecondReplay": True,
            "providerResponseAuthenticated": False,
            "jobPersisted": False, "toolDispatchPersisted": False,
            "observationPersisted": False,
            "agentReadPersisted": False,
            "numericCitationAllowed": False,
            "humanReviewApproved": False,
            "reportPublishAuthorized": False,
            "candidateOnly": True}
        value["observationDigest"] = digest(value)
        return value
    except (AnalysisContractError, DatabaseError, KeyError, TypeError,
            ValueError, StopIteration) as error:
        raise AiError("市场 v6 拥有方观察来源或结果不可核验",
            "market_v2_tool_observation_unverified", 409) from error

"""Test-only owner composition for a future report seal ticket protocol.

No ticket, claim, body, HMAC key, seal, Agent receipt or file is written. The
two protected owner paths are run for the same report/run, then the pure
proposal remains blocked for lack of finance mapping and a v2 signer.
"""
from __future__ import annotations

from django.conf import settings

from business_analysis import (
    v4_report_seal_transaction_protocol_v1 as protocol,
    v4_report_signer_preparation_v1 as signer)
from business_analysis.contracts import AnalysisContractError

from . import (business_v4_report_preflight_owning as linked_owner,
    business_v4_report_seal_admission_owning as stream_owner)
from .policy import AiError,digest,identifier


SCHEMA = "business-v4-report-seal-transaction-owning-v1"


def inspect_candidate(report_id, run_id, principal, *, enabled=False,
                      checkpoint=None):
    if enabled is not True:
        raise AiError("v4报告seal事务拥有方默认关闭","conflict",409)
    if (settings.DJANGO_ENVIRONMENT != "test" or
            settings.DJANGO_PROCESS_ROLE != "development"):
        raise AiError("v4报告seal事务仅允许隔离测试管理员",
            "access_denied",403)
    report_id,run_id=identifier(report_id),identifier(run_id)
    linked=linked_owner.inspect_candidate(report_id,run_id,principal,
        enabled=True)
    streams=stream_owner.inspect_candidate(run_id,principal,
        enabled=True,checkpoint=checkpoint)
    try:
        signer_receipt=signer.inspect_candidate(streams,
            finance_mapping_witness=None,enabled=True)
        proposal=protocol.prepare_candidate(linked,streams,signer_receipt,
            finance_mapping_witness=None,enabled=True)
        trace=protocol.inspect_trace(proposal,[],enabled=True)
    except (AnalysisContractError,KeyError,TypeError) as error:
        raise AiError("v4报告seal事务证据不能建立同任务候选",
            "conflict",409) from error
    value={"schemaVersion":SCHEMA,
        "status":proposal["status"],
        "reportId":report_id,
        "v4RunId":run_id,
        "linkedOwnerDigest":linked["resultDigest"],
        "streamOwnerDigest":streams["resultDigest"],
        "proposalDigest":proposal["proposalDigest"],
        "traceDigest":trace["resultDigest"],
        "bothOwnerPathsInvoked":True,
        "financeMappingAuthorityVerified":False,
        "protectedTicketIssued":False,
        "protectedClaimed":False,
        "protectedSealCommitted":False,
        "protectedConsumptionRecorded":False,
        "authorityVerified":False,
        "reportGenerationSupported":False,
        "rendererRegistered":False,
        "downloadSupported":False}
    return {**value,"resultDigest":digest(value)}

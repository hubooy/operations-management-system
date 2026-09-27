"""Test-only owner invocation before a future protected v4 report signer.

Calling this never loads a signing key, calls the legacy sign() oracle, writes a
seal/ticket, or enables a report. The finance mapping authority is absent.
"""
from __future__ import annotations

from django.conf import settings

from business_analysis import v4_report_signer_preparation_v1 as pure
from business_analysis.contracts import AnalysisContractError

from . import business_v4_report_seal_admission_owning as source_owner
from .policy import AiError, digest, identifier


SCHEMA = "business-v4-report-signer-preparation-owning-v1"


def inspect_candidate(run_id, principal, *, enabled=False,
                      checkpoint=None):
    if enabled is not True:
        raise AiError("v4可报告封存signer拥有方默认关闭", "conflict", 409)
    if (settings.DJANGO_ENVIRONMENT != "test" or
            settings.DJANGO_PROCESS_ROLE != "development"):
        raise AiError("v4可报告封存signer准备仅供隔离测试管理员",
            "access_denied", 403)
    run_id = identifier(run_id)
    owned = source_owner.inspect_candidate(run_id,principal,
        enabled=True,checkpoint=checkpoint)
    try:
        candidate = pure.inspect_candidate(owned,
            finance_mapping_witness=None,enabled=True)
    except (AnalysisContractError,KeyError,TypeError) as error:
        raise AiError("v4可报告封存signer准备仍缺保护证明",
            "conflict",409) from error
    value = {"schemaVersion": SCHEMA,
        "status": candidate["status"],
        "runId": run_id,
        "ownerAdmissionDigest": owned["resultDigest"],
        "signerPreparationDigest": candidate["candidateDigest"],
        "sourceOwnerCalledInThisOperation": True,
        "financeMappingAuthorityVerified": False,
        "legacySealReinterpreted": False,
        "keyMaterialLoaded": False,
        "signerAuthorized": False,
        "reportCapableSealIssued": False,
        "reportGenerationSupported": False,
        "rendererRegistered": False,
        "downloadSupported": False}
    return {**value,"resultDigest":digest(value)}

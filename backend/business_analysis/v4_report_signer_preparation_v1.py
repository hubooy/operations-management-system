"""Closed preparation boundary for a *new* report-capable v4 seal protocol.

The legacy parent-seal HMAC purpose and signed false policy cannot be reused.
No protected finance shop-mapping witness or new signer identity is installed,
so this module never emits a body, MAC, key ID or signing permission.
"""
from __future__ import annotations

import hashlib
import re

from . import v4_final_commit_contract
from .contracts import AnalysisContractError, digest


SCHEMA = "business-v4-report-signer-preparation-v1"
NEW_SEAL_SCHEMA = "business-v4-report-capable-seal-v2-proposed"
NEW_PURPOSE = b"teruisi:business-v4:report-capable-seal:v1\x00"
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _need(ok, message="v4可报告封存签发前置证据不足"):
    if not ok:
        raise AnalysisContractError(message)


def inspect_candidate(owner_admission, *, finance_mapping_witness=None,
                      enabled=False):
    """Describe non-signability after a real owner replay, without a key."""
    _need(enabled is True, "v4可报告封存signer准备默认关闭")
    _need(finance_mapping_witness is None,
        "当前版本没有财报同店拥有方证据目录，不接受调用方证明")
    receipt = owner_admission
    _need(type(receipt) is dict and receipt.get("schemaVersion") ==
        "business-v4-report-seal-admission-owning-v1"
        and receipt.get("status") ==
            "unavailable_report_capable_seal_not_issued"
        and receipt.get("resultDigest") == digest({key: value for key,value
            in receipt.items() if key != "resultDigest"})
        and receipt.get("threeRealOwningStreamsReplayedTwice") is True
        and receipt.get("legacyApplicationHmacRechecked") is True
        and all(receipt.get(key) is False for key in (
            "financeShopMappingAuthorityVerified",
            "reportCapableSealIssued", "usableFor13Tables",
            "usableForVolumePublication", "usableForAgentOrDownload")))
    for key in ("legacySealedDigest", "decisionDigest"):
        value = receipt.get(key)
        _need(type(value) is str and _SHA.fullmatch(value) is not None)
    old = v4_final_commit_contract.PARENT_SEAL_PURPOSE
    _need(NEW_PURPOSE != old and NEW_PURPOSE.endswith(b"\x00")
        and old.endswith(b"\x00"))
    value = {"schemaVersion": SCHEMA,
        "status": "blocked_no_finance_mapping_or_new_signer",
        "runId": receipt["runId"],
        "legacySealedDigest": receipt["legacySealedDigest"],
        "ownerDecisionDigest": receipt["decisionDigest"],
        "proposedSealSchema": NEW_SEAL_SCHEMA,
        "legacyPurposeSha256": hashlib.sha256(old).hexdigest(),
        "newPurposeSha256": hashlib.sha256(NEW_PURPOSE).hexdigest(),
        "purposeSeparatedFromLegacy": True,
        "requiredNextProofs": [
            "independent_finance_shop_mapping_owner_witness",
            "protected_new_version_seal_key_and_signer_identity",
            "one_time_sql_ticket_and_consumption_receipt",
            "new_seal_full_source_hmac_and_three_window_day_roots",
            "versioned_same_report_13_table_and_volume_publisher"],
        "canonicalSignableBody": None,
        "newKeyId": None,
        "newBodyMac": None,
        "keyMaterialLoaded": False,
        "signerAuthorized": False,
        "reportCapableSealIssued": False,
        "reportGenerationSupported": False,
        "agentCitationSupported": False,
        "rendererRegistered": False,
        "downloadSupported": False}
    return {**value, "candidateDigest": digest(value)}

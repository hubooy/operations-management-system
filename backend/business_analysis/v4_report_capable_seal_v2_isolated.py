"""Isolated-only shape for a future report-capable v4 seal body.

This module cannot sign, read a key, authenticate a finance owner, or issue a
seal. A test may supply independent random keys and an HMAC-checked synthetic
finance witness to examine byte/role separation. The resulting body is marked
testVectorOnly and never gains report authority.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
import re

from . import v4_report_seal_transaction_protocol_v1 as protocol
from .contracts import AnalysisContractError, canonical, digest
from .v4_report_signer_preparation_v1 import NEW_PURPOSE


SCHEMA = "business-v4-report-capable-seal-v2-isolated-vector-v1"
FINANCE_SCHEMA = "finance-shop-map-isolated-witness-v1"
FINANCE_ROLE = "teruisi_finance_shop_attestor_test"
SIGNER_ROLE = "teruisi_ai_v4_report_seal_signer_test"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_KEY_ID = re.compile(r"[0-9a-f]{16}\Z")
BODY_FIELDS = frozenset(("schemaVersion","reportId","v4RunId",
    "shop","originalPeriod","v2SealedDigest","legacyV4SealedDigest",
    "sourceBindingsDigest","periodPlanDigest","threeWindowRootsDigest",
    "financeMappingProofDigest","financeScopeKey",
    "financeSourceRef","financeSourceRevision","keyId",
    "purposeSha256","reportGenerationSupported","agentDispatchSupported",
    "rendererRegistered","downloadSupported","humanReviewRequired",
    "testVectorOnly","authorityVerified"))


def _need(ok, message="隔离v4报告seal正文或财报映射不具权威"):
    if not ok:
        raise AnalysisContractError(message)


def inspect_test_vector(proposal, mapping_witness, body_json, *,
                        mapping_mac_verified=False, signer_role=None,
                        enabled=False):
    """Validate synthetic bytes only; no old/new signing operation exists."""
    _need(enabled is True, "隔离v4报告seal向量默认关闭")
    _need(type(proposal) is dict and proposal.get("schemaVersion") ==
        protocol.SCHEMA and proposal.get("status") ==
            "blocked_no_protected_finance_map_or_v2_signer"
        and proposal.get("proposalDigest") == digest({key:value for key,value
            in proposal.items() if key!="proposalDigest"})
        and proposal.get("requiredFinanceMappingWitnessDigest") is None
        and proposal.get("signableBody") is None
        and proposal.get("requiredTransactionOrder") ==
            list(protocol.ORDER)
        and proposal.get("issued") is False
        and proposal.get("claimed") is False
        and proposal.get("committed") is False)
    for key in ("v2SealedDigest","legacyV4SealedDigest",
                "sourceBindingsDigest","periodPlanDigest",
                "threeWindowRootsDigest","financeSourceRef",
                "streamOwnerResultDigest","newPurposeSha256"):
        value=proposal.get(key)
        _need(type(value) is str and _SHA.fullmatch(value) is not None)
    _need(signer_role == SIGNER_ROLE and mapping_mac_verified is True,
        "测试签发角色或独立财报MAC核验不匹配")
    mapping = mapping_witness
    _need(type(mapping) is dict and set(mapping) == {
        "schemaVersion","ownerRole","reportId","v4RunId","platform",
        "shop","scopeKey","originalPeriod","financeSourceRef",
        "financeSourceRevision","publishedMonths","sourceRowsChecked",
        "testVectorOnly","proofDigest"}
        and mapping["schemaVersion"] == FINANCE_SCHEMA
        and mapping["ownerRole"] == FINANCE_ROLE
        and mapping["testVectorOnly"] is True
        and mapping["sourceRowsChecked"] is True
        and mapping["proofDigest"] == digest({key:value for key,value in
            mapping.items() if key!="proofDigest"})
        and mapping["reportId"] == proposal["reportId"]
        and mapping["v4RunId"] == proposal["v4RunId"]
        and mapping["platform"] == "京东"
        and mapping["shop"] == proposal["shop"]
        and mapping["scopeKey"] == "shop:京东:"+proposal["shop"]
        and mapping["originalPeriod"] == proposal["originalPeriod"]
        and mapping["financeSourceRef"] == proposal["financeSourceRef"]
        and mapping["financeSourceRevision"] == proposal[
            "financeSourceRevision"]
        and type(mapping["financeSourceRef"]) is str
        and _SHA.fullmatch(mapping["financeSourceRef"]) is not None
        and type(mapping["financeSourceRevision"]) is str
        and mapping["financeSourceRevision"])
    months = mapping["publishedMonths"]
    _need(type(months) is list and months == sorted(set(months))
        and all(type(item) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}",item)
            for item in months))
    start = date.fromisoformat(proposal["originalPeriod"]["startDate"])
    end = date.fromisoformat(proposal["originalPeriod"]["endDate"])
    needed=set()
    year,month=start.year,start.month
    while (year,month)<=(end.year,end.month):
        needed.add(f"{year:04d}-{month:02d}")
        month += 1
        if month==13:
            year,month=year+1,1
    _need(start <= end and needed <= set(months),
        "财报合成证据没有覆盖分析范围触及的自然月")
    _need(type(body_json) is str and len(body_json.encode("utf-8"))<=65536)
    try:
        body=json.loads(body_json)
    except (TypeError,ValueError) as error:
        raise AnalysisContractError("隔离seal正文不可解析") from error
    _need(type(body) is dict and set(body)==BODY_FIELDS
        and canonical(body)==body_json
        and body["schemaVersion"]==SCHEMA
        and body["reportId"]==proposal["reportId"]
        and body["v4RunId"]==proposal["v4RunId"]
        and body["shop"]==proposal["shop"]
        and body["originalPeriod"]==proposal["originalPeriod"]
        and all(body[key]==proposal[key] for key in (
            "v2SealedDigest","legacyV4SealedDigest",
            "sourceBindingsDigest","periodPlanDigest",
            "threeWindowRootsDigest"))
        and body["financeMappingProofDigest"]==mapping["proofDigest"]
        and body["financeScopeKey"]==mapping["scopeKey"]
        and body["financeSourceRef"]==proposal["financeSourceRef"]
        and body["financeSourceRevision"]==proposal[
            "financeSourceRevision"]
        and type(body["keyId"]) is str
        and _KEY_ID.fullmatch(body["keyId"]) is not None
        and body["purposeSha256"]==hashlib.sha256(NEW_PURPOSE).hexdigest()
        and body["reportGenerationSupported"] is True
        and body["agentDispatchSupported"] is False
        and body["rendererRegistered"] is False
        and body["downloadSupported"] is False
        and body["humanReviewRequired"] is True
        and body["testVectorOnly"] is True
        and body["authorityVerified"] is False)
    result={"schemaVersion":"business-v4-report-seal-isolated-check-v1",
        "reportId":body["reportId"],"v4RunId":body["v4RunId"],
        "bodyDigest":hashlib.sha256(body_json.encode("utf-8")).hexdigest(),
        "financeMappingProofDigest":mapping["proofDigest"],
        "syntheticShapeVerified":True,
        "independentTestKeyMacVerifiedByThisFunction":False,
        "protectedFinanceOwnerVerified":False,
        "protectedReportSignerVerified":False,
        "reportCapableSealIssued":False,
        "authorityVerified":False,
        "rendererRegistered":False,
        "downloadSupported":False}
    return {**result,"resultDigest":digest(result)}

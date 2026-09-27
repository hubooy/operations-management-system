"""Test-only real-owner replay for a future report-capable v4 seal.

The existing verifier authenticates the *legacy* HMAC and all four persisted
source chains. This adapter then independently streams all three promotion
windows twice into O(1) digest sinks. It never signs a new seal or grants any
report, Agent, renderer or download capability.
"""
from __future__ import annotations

import hashlib

from django.conf import settings

from business_analysis import (v4_report_seal_admission_v2 as pure,
    report_v4_promotion_stream_candidate as stream)
from business_analysis.contracts import AnalysisContractError

from . import (business_v4_report_stream_owning_candidate as source_owner,
    business_v4_seal_verify as verifier, models as m)
from .policy import AiError, digest, identifier


SCHEMA = "business-v4-report-seal-admission-owning-v1"


def _need(ok, message="v4可报告封存准入时来源或当前身份变化"):
    if not ok:
        raise AiError(message, "conflict", 409)


def _test_only(enabled):
    _need(enabled is True, "v4可报告封存拥有方候选默认关闭")
    if (settings.DJANGO_ENVIRONMENT != "test" or
            settings.DJANGO_PROCESS_ROLE != "development"):
        raise AiError("v4可报告封存候选仅允许隔离测试管理员",
            "access_denied", 403)


class DigestOnlySink:
    """Count private rows without retaining a file, page or customer row."""
    def __init__(self):
        self.count = 0
        self.root = hashlib.sha256()
        self.receipt = None
        self.aborted = False

    def stage(self, source_key, index, raw):
        _need(not self.aborted and self.receipt is None
            and index == self.count)
        self.root.update((source_key+"\x00"+str(index)+"\x00"+raw+"\n").encode(
            "utf-8"))
        self.count += 1

    def complete(self, receipt):
        _need(not self.aborted and self.receipt is None
            and self.count == receipt["rowCount"])
        self.receipt = receipt

    def abort(self):
        self.aborted = True
        self.count = 0
        self.root = hashlib.sha256()
        self.receipt = None


def _bridge(parent, sources, verified, plan, period):
    refs = {item["sourceKey"]: item for item in verified["sourceRefs"]}
    directory = {item.source_key: item for item in sources}
    _need(len(refs) == len(directory) == 4 and set(refs) == set(directory))
    windows = []
    for window in pure.WINDOWS:
        row = next(item for item in period["dailySources"]
            if item["window"] == window)
        physical, ref = directory[row["sourceKey"]], refs[row[
            "sourceKey"]]
        _need(physical.query_digest == row["queryDigest"]
            and physical.source_identity_digest ==
                row["sourceIdentityDigest"]
            and physical.source_ref == ref["sourceRef"]
            and physical.source_revision == ref["sourceRevision"] ==
                ref["liveRevision"]
            and ref["verificationFreshness"] == "current_revision")
        windows.append({"window": window,
            "sourceKey": row["sourceKey"],
            "queryDigest": row["queryDigest"],
            "sourceIdentityDigest": row["sourceIdentityDigest"],
            "sourceRef": physical.source_ref,
            "sourceRevision": physical.source_revision,
            "resolvedPeriod": row["resolvedPeriod"],
            "expectedDayDigest": row["expectedDayDigest"]})
    day = next(item for item in period["dailySources"] if item[
        "window"] == "current")
    body = {"schemaVersion":
        "business-report-v4-unbound-owning-candidate-v1",
        "v4RunId": parent.id,
        "v4EvidenceVersion": parent.version,
        "v4SealedDigest": verified["sealedDigest"],
        "shop": day["shop"],
        "originalPeriod": {"startDate": day["originalQueryStartDate"],
            "endDate": day["originalQueryEndDate"]},
        "promotionWindows": windows,
        "candidateOnly": True,
        "persistedSameReportLinkVerified": False,
        "v4RowsReadableForReport": False,
        "agentCitationSupported": False,
        "registeredRenderer": False, "publishable": False}
    return {**body,"candidateDigest":digest(body)}


def inspect_candidate(run_id, principal, *, enabled=False,
                      checkpoint=None):
    """HMAC+all-three double-pass proof, then explicit unavailable verdict."""
    _test_only(enabled)
    run_id = identifier(run_id)
    actor,parent,sources,directory,verified,body,plan,period,seal_state = (
        source_owner._v4(run_id,principal))
    finance = period["financeContext"]
    expected_scope = {"scope_key":"shop:京东:"+period["dailySources"][0][
        "shop"], "scope_type":"shop", "scope_name":period[
        "dailySources"][0]["shop"]}
    finance_item = next(item for item in body["sources"] if item[
        "sourceKey"] == finance["sourceKey"])
    scope = finance_item["scope"]
    _need(type(scope) is dict
        and all(scope.get(key) == value for key,value in
            expected_scope.items())
        and scope == finance["scope"]
        and finance_item["missingMonths"] == [],
        "财报不是精确同店自然月，禁止报告封存准入")
    bridge = _bridge(parent,sources,verified,plan,period)
    initial = [(item.id,item.version,item.source_ref,
        item.source_revision,item.checkpoint_json) for item in sources]

    def current(_bridge,_source):
        current_actor,current_parent,current_sources,current_directory = (
            verifier._directory(run_id,principal))
        current_seal = m.AiBusinessV4Seal.objects.filter(
            run_id=run_id).values("body_digest","body_json","body_mac",
                "key_id").first()
        return (current_actor == actor
            and current_parent.version == parent.version
            and current_directory == directory
            and [(item.id,item.version,item.source_ref,
                item.source_revision,item.checkpoint_json) for item in
                current_sources] == initial
            and current_seal == seal_state)

    receipts = []
    try:
        for window in pure.WINDOWS:
            chosen = next(item for item in bridge["promotionWindows"]
                if item["window"] == window)
            source = next(item for item in sources if item.source_key ==
                chosen["sourceKey"])
            sealed_source = next(item for item in body["sources"]
                if item["sourceKey"] == source.source_key)
            manifest = source_owner._source_manifest(bridge,source,
                sealed_source)
            sink = DigestOnlySink()
            receipt = stream.stage_candidate(bridge,window,manifest,
                lambda: source_owner._pages(parent,source,actor),sink,
                current,enabled=True,checkpoint=checkpoint)
            _need(sink.receipt == receipt and not sink.aborted)
            receipts.append(receipt)
        decision = pure.inspect_candidate(plan,verified,
            m.AiBusinessV4Seal.objects.get(run_id=run_id).body_json,
            receipts,finance_mapping_witness=None,enabled=True)
        final = source_owner._v4(run_id,principal)
        _need(final[1].version == parent.version
            and final[3] == directory and final[4] == verified
            and final[8] == seal_state and current(bridge,None),
            "三期重放后HMAC、目录或当前修订变化")
    except (AnalysisContractError,KeyError,TypeError,ValueError) as error:
        raise AiError("v4可报告封存来源完整性或店铺归属不足",
            "conflict",409) from error
    value = {"schemaVersion":SCHEMA,
        "status":decision["status"],
        "runId":run_id,
        "shop":bridge["shop"],
        "originalPeriod":bridge["originalPeriod"],
        "legacySealedDigest":verified["sealedDigest"],
        "decisionDigest":decision["candidateDigest"],
        "periodPlanDigest":decision["periodPlanDigest"],
        "threeWindowRootsDigest":digest(decision["promotionStreamRoots"]),
        "financeSourceKey":decision["financeSourceKey"],
        "financeSourceRef":finance_item["sourceRef"],
        "financeSourceRevision":finance_item["sourceRevision"],
        "threeRealOwningStreamsReplayedTwice":True,
        "legacyApplicationHmacRechecked":True,
        "financeShopMappingAuthorityVerified":False,
        "reportCapableSealIssued":False,
        "usableFor13Tables":False,
        "usableForVolumePublication":False,
        "usableForAgentOrDownload":False}
    return {**value,"resultDigest":digest(value)}

"""Disabled-by-default, single-source/single-segment v4 replay orchestration.

The caller injects an authenticated autocommit sealer connection, a derived
32-byte segment MAC key, and an already claimed ticket. This module never
issues tickets, opens a connection, activates a role, or calls seal.
"""
from __future__ import annotations

import hashlib

from .contracts import AnalysisContractError, canonical, digest
from .v4_sealer_finance_segment_replay import replay_finance_segment
from .v4_sealer_mac import segment_key_id, verify_segment
from .v4_sealer_segment_replay import HEX64, _json, replay_promotion_segment


def _require(ok, reason):
    if not ok:
        raise AnalysisContractError(reason)


def _one(db, function, args, names):
    # Function names are fixed locally; values always use driver parameters.
    with db.cursor() as cursor:
        cursor.execute("SELECT * FROM public." + function + "(" +
                       ",".join(["%s"] * len(args)) + ")", args)
        rows = cursor.fetchmany(2)
        _require(len(rows) <= 1, "v4受保护函数返回重复行")
        if not rows:
            return None
        row = rows[0]
        _require(len(row) == len(names), "v4受保护函数返回列数不符")
        return dict(zip(names, row))


CONTEXT = ("run_id attempt_id parent_status parent_version plan_json "
           "plan_digest directory_digest key_id source_count seal_body_json "
           "seal_body_digest seal_body_mac run_bound_capability_verified").split()
SOURCE = ("source_id source_key ordinal domain temporal_role query_json "
          "query_digest source_identity_digest source_revision_hint source_version "
          "source_ref source_revision page_count row_count stored_bytes source_root "
          "key_id checkpoint_digest last_chunk_digest metadata_json "
          "finance_state_digest run_bound_capability_verified").split()
SEGMENT = ("segment_id source_version source_ref source_revision start_sequence "
           "end_sequence previous_segment_digest progress_json progress_digest "
           "proof_digest proof_mac run_bound_capability_verified").split()
PAGE = ("source_key domain source_version source_ref source_revision segment_id "
        "chunk_id page_sequence row_count payload_json payload_digest payload_bytes "
        "audit_id request_id invocation_id tool_name arguments_digest response_digest "
        "audit_created_at run_bound_capability_verified").split()
RECEIPT = "candidate_json candidate_digest recorded_at".split()
WRITE = "candidate_digest recorded_at".split()


def _receipt(db, args, index):
    return _one(db, "ai_v4_sealer_replay_progress",
                [args[0], args[1], args[2], index, *args[3:]], RECEIPT)


def _stored(row, identity, index, domain):
    _require(row is not None, "v4前段受保护回执不存在")
    candidate = _json(row["candidate_json"], 48_000)
    schema = ("business-v4-sealer-promotion-segment-candidate-v2"
              if domain == "netshop" else
              "business-v4-sealer-finance-segment-candidate-v2")
    _require(type(candidate) is dict and candidate.get("schemaVersion") == schema
             and candidate.get("financeReplayed") is (domain == "finance")
             and candidate.get("candidateOnly") is True
             and candidate.get("authorityVerified") is False
             and candidate.get("sealCommitted") is False
             and candidate.get("candidateDigest") == row["candidate_digest"]
             and digest({k: v for k, v in candidate.items()
                         if k != "candidateDigest"}) == row["candidate_digest"]
             and all(candidate.get(k) == identity[v] for k, v in (
                 ("runId", "runId"), ("attemptId", "attemptId"),
                 ("sourceId", "sourceId"), ("sourceRoot", "sourceRoot"),
                 ("sourceKey", "sourceKey"), ("sourceRef", "sourceRef"),
                 ("sourceRevision", "sourceRevision"),
                 ("sourceVersion", "sourceVersion"), ("keyId", "keyId")))
             and candidate.get("segmentIndex") == index,
             "v4受保护回执身份或摘要不符")
    return candidate


def replay_one_claimed_segment(db, derived_key, *, run_id, attempt_id,
                               source_id, segment_index, actor_email,
                               actor_version, nonce, claim, enabled=False):
    """Return only candidate status; a write exception has unknown outcome.

    `db` must already be authenticated as the restricted sealer session and
    `autocommit` must be true. Claim material is accepted solely for fixed
    SECURITY DEFINER calls and is never included in a return value.
    """
    _require(enabled is True, "v4单段编排默认关闭")
    _require(getattr(db, "autocommit", None) is True,
             "v4单段编排要求已认证autocommit连接")
    _require(type(segment_index) is int and 1 <= segment_index <= 1024,
             "v4单段序号无效")
    key_id = segment_key_id(derived_key)
    args = [run_id, attempt_id, actor_email, actor_version, nonce, claim]
    context = _one(db, "ai_v4_sealer_ticket_context", args, CONTEXT)
    _require(context is not None and context["run_bound_capability_verified"] is True
             and context["run_id"] == run_id
             and context["attempt_id"] == attempt_id
             and context["key_id"] == key_id
             and type(context["source_count"]) is int
             and context["source_count"] >= 1,
             "v4当前claim上下文或密钥身份无效")
    for raw, hash_value, limit in ((context["plan_json"], context["plan_digest"], 131072),):
        _json(raw, limit)
        _require(hashlib.sha256(raw.encode("utf-8")).hexdigest() == hash_value,
                 "v4上下文原文摘要不符")
    if context["seal_body_json"] is not None:
        raw = context["seal_body_json"]
        _json(raw, 32768)
        _require(hashlib.sha256(raw.encode("utf-8")).hexdigest() ==
                 context["seal_body_digest"], "v4封存正文摘要不符")
    else:
        _require(context["seal_body_digest"] is None and
                 context["seal_body_mac"] is None,
                 "v4未封存上下文残留封存正文")
    source = _one(db, "ai_v4_sealer_ticket_source",
                  [run_id, attempt_id, source_id, *args[2:]], SOURCE)
    _require(source is not None and source["run_bound_capability_verified"] is True
             and source["source_id"] == source_id
             and source["key_id"] == key_id
             and type(source["ordinal"]) is int
             and 1 <= source["ordinal"] <= context["source_count"]
             and source["domain"] in {"netshop", "finance"}
             and ((source["domain"] == "netshop" and
                   source["temporal_role"] == "daily_fact") or
                  (source["domain"] == "finance" and
                   source["temporal_role"] == "monthly_context")),
             "v4来源与claim上下文不符")
    query = _json(source["query_json"], 4096)
    _require(hashlib.sha256(source["query_json"].encode("utf-8")).hexdigest()
             == source["query_digest"], "v4来源查询摘要不符")
    plan = _json(context["plan_json"], 131072)
    plans = plan.get("sourcePlans") if type(plan) is dict else None
    _require(type(plans) is list and len(plans) == context["source_count"]
             and plan.get("sourceCount") == context["source_count"]
             and type(plans[source["ordinal"] - 1]) is dict
             and all(plans[source["ordinal"] - 1].get(k) == v for k, v in (
                 ("sourceKey", source["source_key"]),
                 ("ordinal", source["ordinal"]),
                 ("domain", source["domain"]),
                 ("temporalRole", source["temporal_role"]),
                 ("query", query),
                 ("queryDigest", source["query_digest"]),
                 ("sourceIdentityDigest", source["source_identity_digest"]),
                 ("sourceRevisionHint", source["source_revision_hint"]))),
             "v4来源与上下文计划不符")
    _require(type(source["source_root"]) is str
             and HEX64.fullmatch(source["source_root"]) is not None
             and type(source["page_count"]) is int
             and 1 <= source["page_count"] <= 16384
             and type(source["source_version"]) is int
             and source["source_version"] == source["page_count"] + 1
             and segment_index <= (source["page_count"] + 15) // 16,
             "v4来源根或段容量无效")
    identity = {"runId": run_id, "attemptId": attempt_id,
                "actorEmail": actor_email, "actorVersion": actor_version,
                "sourceId": source_id, "sourceKey": source["source_key"],
                "sourceRoot": source["source_root"],
                "sourceVersion": source["source_version"],
                "sourcePageCount": source["page_count"],
                "sourceRowCount": source["row_count"],
                "sourceStoredBytes": source["stored_bytes"],
                "sourceRef": source["source_ref"],
                "sourceRevision": source["source_revision"],
                "keyId": key_id, "query": query}
    receipt_args = [run_id, attempt_id, source_id, actor_email,
                    actor_version, nonce, claim]
    existing = _receipt(db, receipt_args, segment_index)
    if existing is not None:
        old = _stored(existing, identity, segment_index, source["domain"])
        return {"status": "existing_candidate", "candidateOnly": True,
                "authorityVerified": False,
                "candidateDigest": old["candidateDigest"]}
    previous = None
    if segment_index > 1:
        previous = _stored(_receipt(db, receipt_args, segment_index - 1),
                           identity, segment_index - 1, source["domain"])
    segment = _one(db, "ai_v4_sealer_ticket_segment",
                   [run_id, attempt_id, source_id, segment_index,
                    *args[2:]], SEGMENT)
    _require(segment is not None, "v4段证明缺失")
    segment["segment_index"] = segment_index
    start = (segment_index - 1) * 16 + 1
    end = min(segment_index * 16, source["page_count"])
    _require(segment["start_sequence"] == start
             and segment["end_sequence"] == end,
             "v4段范围与来源页数不符")
    pages = []
    for sequence in range(start, end + 1):
        item = _one(db, "ai_v4_sealer_ticket_page",
                    [run_id, attempt_id, source_id, sequence,
                     *args[2:]], PAGE)
        _require(item is not None, "v4段原文页缺失")
        pages.append(item)
    # A second claim-bound read occurs after all pages and before replay/write.
    again = _one(db, "ai_v4_sealer_ticket_context", args, CONTEXT)
    _require(again == context, "v4读取期间claim上下文变化")
    replay = (replay_promotion_segment if source["domain"] == "netshop"
              else replay_finance_segment)
    candidate = replay(identity, segment, pages, previous=previous,
        verify_claim=lambda *_: again == context,
        verify_segment_mac=lambda payload, mac: verify_segment(
            payload, mac, derived_key, key_id),
        verify_previous_result=lambda value, *_: value == previous)
    _require(candidate["candidateOnly"] is True
             and candidate["authorityVerified"] is False,
             "v4候选错误声称权威")
    raw = canonical(candidate)
    try:
        written = _one(db, "ai_v4_sealer_record_replay_progress",
                       [run_id, attempt_id, source_id, segment_index,
                        *args[2:], raw], WRITE)
    except Exception:
        # The commit boundary is unknowable after a driver/server exception.
        return {"status": "unknown_write_result", "candidateOnly": True,
                "authorityVerified": False}
    _require(written is not None and written["candidate_digest"] ==
             candidate["candidateDigest"], "v4写入回执摘要不符")
    saved = _receipt(db, receipt_args, segment_index)
    _require(saved is not None and saved["candidate_json"] == raw
             and saved["candidate_digest"] == candidate["candidateDigest"]
             and saved["recorded_at"] == written["recorded_at"],
             "v4写后精确回读不一致")
    return {"status": "recorded_candidate", "candidateOnly": True,
            "authorityVerified": False,
            "candidateDigest": candidate["candidateDigest"]}

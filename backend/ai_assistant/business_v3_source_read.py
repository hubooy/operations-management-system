"""Signed internal v3 source slices with one bounded full-seal preparation.

The handle is an expiring HMAC proof of an earlier full seal verification. It
is not an Agent-read receipt and cannot start the paused workflow intent.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time

from django.conf import settings

from . import business_evidence_v3 as owner, business_v3_catalog as catalog
from . import business_v3_report_intent as intents, models as m
from . import business_v3_tool_receipts as receipt_contract
from .policy import AiError, canonical, digest, identifier, integer

DIRECTORY_SCHEMA = "business-v3-source-directory-v1"
SLICE_SCHEMA = "business-v3-source-slice-v1"
HANDLE_SCHEMA = "business-v3-prepared-read-handle-v1"
HANDLE_SECONDS = 600
MAX_PREPARED_PAGES = 64
MAX_PREPARED_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 38_000
DIRECTORY_LIMIT = 10
MAX_ROWS = 10
PURPOSE = b"teruisi:business-v3:prepared-reader:v1\x00"


def _reject(message="v3内部来源读取身份或封存证明失效", code="conflict", status=409):
    raise AiError(message, code, status)


def _key():
    secret = settings.DJANGO_INTERNAL_SECRET
    if type(secret) is not str or len(secret) < 32:
        _reject("内部读取签名密钥不可用", "service_unavailable", 503)
    return hmac.new(secret.encode("utf-8"), PURPOSE, hashlib.sha256).digest()


def _seal_state(parent):
    try:
        value = json.loads(parent.state_json)
        if (type(value) is not dict or value.get("schemaVersion") != "business-evidence-seal-v3"
                or parent.state_json != canonical(value)
                or value.get("sealedDigest") != digest({k: v for k, v in value.items()
                    if k != "sealedDigest"})):
            _reject()
        return value
    except (ValueError, TypeError, RecursionError) as error:
        raise AiError("v3封存状态不可读取", "conflict", 409) from error


def _context(intent_id, principal):
    actor = owner._actor(principal)
    intent = m.AiBusinessV3ReportIntent.objects.filter(pk=identifier(intent_id)).first()
    if (intent is None or intent.owner_email != principal.email.lower()
            or intent.scope_json != "null" or intent.status != "paused"
            or intent.pause_reason != "v3_agents_not_registered"):
        _reject("v3暂停意图不存在或当前账号无权读取", "not_found", 404)
    parent = m.AiBusinessEvidenceRun.objects.filter(pk=intent.evidence_run_id).first()
    if (parent is None or parent.owner_email != intent.owner_email or parent.status != "sealed"
            or parent.collection_status != "manual" or parent.version != intent.evidence_version
            or intent.snapshot_digest != digest(intent.snapshot_json)):
        _reject()
    state = _seal_state(parent)
    if (state["sealedDigest"] != intent.sealed_digest
            or state["evidenceVersion"] != parent.version
            or state["runId"] != parent.id):
        _reject()
    try:
        snapshot = json.loads(intent.snapshot_json)
        if (type(snapshot) is not dict or snapshot.get("candidateDigest") != intent.candidate_digest
                or snapshot.get("reference", {}).get("sealedDigest") != intent.sealed_digest
                or snapshot.get("reportGenerationSupported") is not False
                or snapshot.get("modelDispatchSupported") is not False):
            _reject()
    except (ValueError, TypeError, AttributeError) as error:
        raise AiError("v3暂停意图快照不可读取", "conflict", 409) from error
    return actor, intent, parent, state


def _final_fence(actor, intent, parent, state, principal, token, source_key):
    """Cheap current principal and immutable parent/intent recheck before bytes leave."""
    current_actor, current_intent, current_parent, current_state, _ = _verify_handle(
        token, intent.id, principal, source_key)
    if (current_actor != actor or current_intent.id != intent.id
            or current_intent.snapshot_digest != intent.snapshot_digest
            or current_intent.candidate_digest != intent.candidate_digest
            or current_parent.id != parent.id or current_parent.version != parent.version
            or current_parent.state_json != parent.state_json
            or current_state["sealedDigest"] != state["sealedDigest"]):
        _reject("v3来源读取结束时账号或封存身份变化", "access_denied", 403)


def _mint(intent, parent, state, actor, source_key, source_proof=None):
    payload = {"schemaVersion": HANDLE_SCHEMA, "intentId": intent.id,
        "runId": parent.id, "ownerEmail": intent.owner_email,
        "actorVersion": actor["version"], "evidenceVersion": parent.version,
        "sealedDigest": state["sealedDigest"], "sealStateDigest": digest(parent.state_json),
        "candidateDigest": intent.candidate_digest, "snapshotDigest": intent.snapshot_digest,
        "sourceKey": source_key, "sourceProofDigest": digest(source_proof) if source_proof else None,
        "expiresAt": int(time.time()) + HANDLE_SECONDS,
        "nonce": secrets.token_urlsafe(16)}
    raw = canonical(payload).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = hmac.new(_key(), raw, hashlib.sha256).hexdigest()
    return encoded + "." + signature


def _verify_handle(token, intent_id, principal, source_key):
    if type(token) is not str or len(token) > 2400 or not re.fullmatch(
            r"[A-Za-z0-9_-]+\.[0-9a-f]{64}", token):
        _reject("v3临时读取句柄无效", "access_denied", 403)
    encoded, signature = token.split(".", 1)
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        value = json.loads(raw)
    except (ValueError, TypeError, UnicodeDecodeError, RecursionError) as error:
        raise AiError("v3临时读取句柄不可解码", "access_denied", 403) from error
    if (len(raw) > 1700 or raw != canonical(value).encode("utf-8")
            or not hmac.compare_digest(hmac.new(_key(), raw, hashlib.sha256).hexdigest(), signature)
            or type(value) is not dict or set(value) != {"schemaVersion", "intentId", "runId",
                "ownerEmail", "actorVersion", "evidenceVersion", "sealedDigest", "sealStateDigest",
                "candidateDigest", "snapshotDigest", "sourceKey", "sourceProofDigest", "expiresAt", "nonce"}
            or value["schemaVersion"] != HANDLE_SCHEMA
            or value["intentId"] != intent_id or value["sourceKey"] != source_key
            or type(value["expiresAt"]) is not int or value["expiresAt"] < int(time.time())
            or value["expiresAt"] > int(time.time()) + HANDLE_SECONDS
            or type(value["nonce"]) is not str or not 16 <= len(value["nonce"]) <= 32):
        _reject("v3临时读取句柄签名、用途或期限失效", "access_denied", 403)
    actor, intent, parent, state = _context(intent_id, principal)
    if (value["ownerEmail"] != principal.email.lower() or value["actorVersion"] != actor["version"]
            or value["runId"] != parent.id or value["evidenceVersion"] != parent.version
            or value["sealedDigest"] != state["sealedDigest"]
            or value["sealStateDigest"] != digest(parent.state_json)
            or value["candidateDigest"] != intent.candidate_digest
            or value["snapshotDigest"] != intent.snapshot_digest):
        _reject("v3临时读取句柄对应身份或封存版本变化", "access_denied", 403)
    proof = None if source_key == "directory" else next((item for item in state["sources"]
        if item["sourceKey"] == source_key), None)
    if source_key != "directory" and (proof is None or value["sourceProofDigest"] != digest(proof)):
        _reject("v3句柄不可借给其他来源", "access_denied", 403)
    if source_key == "directory" and value["sourceProofDigest"] is not None:
        _reject("v3目录句柄用途无效", "access_denied", 403)
    return actor, intent, parent, state, proof


def directory(intent_id, body, principal):
    """First page verifies the full seal once; continuation uses a signed handle."""
    if type(body) is not dict or set(body) - {"offset", "handle"}:
        raise AiError("v3目录分页参数无效")
    offset = integer(body.get("offset", 0), "offset", lo=0, hi=47)
    token = body.get("handle")
    if token is None:
        if offset != 0:
            raise AiError("v3目录续页必须携带首次完整核验句柄")
        actor, intent, parent, state = _context(intent_id, principal)
        if parent.stored_bytes > MAX_PREPARED_BYTES:
            _reject("当前v3读取桥不支持超过8MiB的完整封存；不会返回部分目录",
                    "payload_too_large", 413)
        counts = m.AiBusinessEvidenceSource.objects.filter(run_id=parent.id).values_list("page_count", flat=True)
        if sum(counts) > MAX_PREPARED_PAGES:
            _reject("当前v3读取桥不支持超过64事实页的完整封存；不会返回部分目录",
                    "payload_too_large", 413)
        intents.inspect(intent_id, principal)  # Full owner replay and all receipts, once.
        actor, intent, parent, state = _context(intent_id, principal)
        token = _mint(intent, parent, state, actor, "directory")
    else:
        actor, intent, parent, state, _ = _verify_handle(token, intent_id, principal, "directory")
    row, built, records, current_actor = catalog.load(parent.id, principal, allow_sealed=True)
    if (row.version != parent.version or current_actor != actor
            or row.state_json != parent.state_json):
        _reject("v3目录读取期间封存身份变化")
    proofs = {item["sourceKey"]: item for item in state["sources"]}
    items = []
    for entry in built["entries"][offset:offset + DIRECTORY_LIMIT]:
        proof = proofs.get(entry["key"])
        if (proof is None or (proof["domain"], proof["queryDigest"], proof["ordinal"])
                != (entry["domain"], entry["queryDigest"], entry["ordinal"])):
            _reject("v3来源目录与封存证明不一致")
        item = {"sourceKey": entry["key"], "ordinal": entry["ordinal"],
            "domain": entry["domain"], "query": entry["query"],
            "queryDigest": proof["queryDigest"], "sourceRef": proof["sourceRef"],
            "sourceRevision": proof["sourceRevision"], "pageCount": proof["pageCount"],
            "rowCount": proof["rowCount"], "receiptChainDigest": proof["receiptChainDigest"],
            "coverage": proof["coverage"],
            "sourceHandle": _mint(intent, parent, state, actor, entry["key"], proof)}
        trial = {"schemaVersion": DIRECTORY_SCHEMA, "intentId": intent.id,
            "runId": parent.id, "evidenceVersion": parent.version,
            "sealedDigest": state["sealedDigest"], "handle": token,
            "offset": offset, "items": [*items, item],
            "nextOffset": offset + len(items) + 1 if offset + len(items) + 1 < len(built["entries"]) else None,
            "fullSealVerifiedForHandle": True, "agentReadReceiptRecorded": False,
            "readOnlyOperationOnWriterProcess": True,
            "currentReadBridgeCapacitySupported": True}
        if len(canonical(trial).encode("utf-8")) > MAX_RESPONSE_BYTES:
            if not items: _reject("v3单条目录超过签名工具容量", "payload_too_large", 413)
            break
        items.append(item)
    if not items:
        raise AiError("v3目录偏移超出来源数量")
    result = {"schemaVersion": DIRECTORY_SCHEMA, "intentId": intent.id,
        "runId": parent.id, "evidenceVersion": parent.version,
        "sealedDigest": state["sealedDigest"], "handle": token, "offset": offset,
        "items": items, "nextOffset": offset + len(items) if offset + len(items) < len(built["entries"]) else None,
        "fullSealVerifiedForHandle": True, "agentReadReceiptRecorded": False,
        "readOnlyOperationOnWriterProcess": True,
        "currentReadBridgeCapacitySupported": True}
    if len(canonical(result).encode("utf-8")) > MAX_RESPONSE_BYTES:
        _reject("v3目录完整页超过签名工具容量", "payload_too_large", 413)
    _final_fence(actor, intent, parent, state, principal, token, "directory")
    return result


def page(intent_id, source_key, body, principal):
    """Read one immutable chunk slice without replaying the entire run."""
    identifier(source_key)
    if (type(body) is not dict or set(body) - {"handle", "sequence", "rowOffset", "rowLimit"}
            or not {"handle", "sequence"} <= set(body)):
        raise AiError("v3来源页读取参数无效")
    sequence = integer(body["sequence"], "sequence", lo=1, hi=1999)
    offset = integer(body.get("rowOffset", 0), "rowOffset", lo=0, hi=100)
    limit = integer(body.get("rowLimit", MAX_ROWS), "rowLimit", lo=1, hi=MAX_ROWS)
    actor, intent, parent, state, proof = _verify_handle(body["handle"], intent_id, principal, source_key)
    if sequence > proof["pageCount"]:
        raise AiError("v3来源页序号超出封存范围")
    source = m.AiBusinessEvidenceSource.objects.filter(run_id=parent.id,
        source_key=source_key).first()
    if (source is None or not source.finished or source.domain != proof["domain"]
            or source.query_digest != proof["queryDigest"]
            or source.version != proof["sourceVersion"]
            or source.page_count != proof["pageCount"]
            or source.row_count != proof["rowCount"]
            or source.query_digest != digest(source.query_json)):
        _reject("v3来源页目录身份变化")
    chunk = m.AiBusinessEvidenceChunk.objects.filter(run_id=parent.id,
        source_key=source_key, sequence=sequence).first()
    if (chunk is None or chunk.payload_digest != digest(chunk.payload_json)):
        _reject("v3来源页原始字节摘要不符")
    receipt = m.AiBusinessSourceToolReceipt.objects.select_related("audit").filter(chunk_id=chunk.id).first()
    if (receipt is None or receipt.run_id != parent.id or receipt.source_id != source.id
            or receipt.sequence != sequence or receipt.response_digest != chunk.payload_digest
            or receipt.source_ref != proof["sourceRef"]
            or receipt.source_revision != proof["sourceRevision"]
            or receipt.actor_email != actor["email"]
            or receipt.tool_name != receipt_contract.expected_tool(source.domain, sequence)
            or receipt.tool_name != receipt.audit.tool_name
            or receipt.request_id != receipt.audit.request_id
            or receipt.invocation_id != receipt.audit.invocation_id
            or receipt.payload_bytes != len(chunk.payload_json.encode("utf-8"))
            or receipt.audit.status != "succeeded"
            or receipt.audit.actor_role != "admin" or receipt.audit.error_code is not None
            or receipt.audit.response_digest != chunk.payload_digest
            or receipt.audit.actor_email != actor["email"]
            or receipt.audit.surface != "business_collection"
            or receipt.audit.created_at > receipt.created_at):
        _reject("v3来源页缺少精确成功工具收据")
    try:
        raw_page = json.loads(chunk.payload_json)
        if (chunk.payload_json != canonical(raw_page)
                or raw_page.get("sourceRef") != proof["sourceRef"]
                or raw_page.get("sourceRevision") != proof["sourceRevision"]):
            _reject("v3来源页身份与封存证明不符")
        rows_key = "rows" if source.domain == "finance" else "items"
        rows = raw_page[rows_key]
        if type(rows) is not list or offset > len(rows):
            raise AiError("v3来源页行偏移无效")
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        raise AiError("v3来源页无法按领域协议读取", "conflict", 409) from error
    metadata = {key: value for key, value in raw_page.items() if key != rows_key}
    size = min(limit, len(rows) - offset)
    while True:
        if size == 0 and offset < len(rows):
            _reject("v3来源页单行超过签名工具容量", "payload_too_large", 413)
        end = offset + size
        result = {"schemaVersion": SLICE_SCHEMA, "intentId": intent.id,
            "runId": parent.id, "evidenceVersion": parent.version,
            "sealedDigest": state["sealedDigest"], "sourceKey": source_key,
            "domain": source.domain, "queryDigest": source.query_digest,
            "sourceRef": proof["sourceRef"], "sourceRevision": proof["sourceRevision"],
            "receiptChainDigest": proof["receiptChainDigest"],
            "sequence": sequence, "chunkDigest": chunk.payload_digest,
            "rowOffset": offset, "returned": size, "totalRowsInChunk": len(rows),
            "nextRowOffset": end if end < len(rows) else None,
            "completeChunkInResponse": offset == 0 and end == len(rows),
            "rows": rows[offset:end], "metadata": metadata,
            "coverage": proof["coverage"],
            "fullSealVerifiedForHandle": True, "agentReadReceiptRecorded": False,
            "readOnlyOperationOnWriterProcess": True}
        if len(canonical(result).encode("utf-8")) <= MAX_RESPONSE_BYTES:
            _final_fence(actor, intent, parent, state, principal, body["handle"], source_key)
            return result
        if size == 0:
            _reject("v3来源页元数据或单行超过签名工具容量", "payload_too_large", 413)
        size -= 1

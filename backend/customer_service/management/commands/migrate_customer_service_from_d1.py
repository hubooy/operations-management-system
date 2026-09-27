from __future__ import annotations

from datetime import timezone as datetime_timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from customer_service.models import (
    CustomerServiceConversation,
    CustomerServiceDataRevision,
    CustomerServiceDeletionAudit,
    CustomerServiceImportAttempt,
    CustomerServiceImportBatch,
    CustomerServiceImportFingerprint,
    CustomerServiceImportScopeHead,
    CustomerServiceMigrationRun,
    CustomerServiceWriteAuthority,
)


GENERATION_VERSION = "customer-service-d1-to-postgres-v1"
DOMAIN = "customer-service"
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
MIGRATION_MAX_MESSAGES = 2_000
MIGRATION_MAX_MESSAGE_BYTES = 128 * 1024


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _hex(value: object, seed: object) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if HEX_64.fullmatch(candidate) else _sha(seed)


def _parse_json(value: object, fallback: object) -> object:
    if not isinstance(value, str):
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _timestamp(value: object):
    parsed = parse_datetime(str(value or ""))
    if parsed is None:
        raise CommandError("D1 客服时间戳无效")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed.astimezone(datetime_timezone.utc)


def _optional_timestamp(value: object):
    return _timestamp(value) if value else None


def _table(source: sqlite3.Connection, name: str) -> bool:
    return source.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _rows(source: sqlite3.Connection, sql: str, parameters: tuple[object, ...] = ()) -> list[dict[str, object]]:
    return [dict(row) for row in source.execute(sql, parameters).fetchall()]


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    try:
        result = int(value or 0)
    except (TypeError, ValueError) as error:
        raise CommandError(f"D1 客服{label}不是有效整数") from error
    if result < minimum:
        raise CommandError(f"D1 客服{label}小于安全下限")
    return result


def _warnings(value: object) -> dict[str, object]:
    parsed = _parse_json(value, [])
    if isinstance(parsed, dict):
        items = parsed.get("items", [])
        total = parsed.get("totalCount", len(items) if isinstance(items, list) else 0)
    else:
        items = parsed
        total = len(items) if isinstance(items, list) else 0
    normalized = [str(item)[:500] for item in items[:50]] if isinstance(items, list) else []
    return {"items": normalized, "totalCount": max(len(normalized), _integer(total, "告警总数")), "truncated": _integer(total, "告警总数") > len(normalized)}


def _messages(value: object) -> list[dict[str, str]]:
    parsed = _parse_json(value, [])
    if not isinstance(parsed, list) or len(parsed) > MIGRATION_MAX_MESSAGES:
        raise CommandError("D1 客服消息集合无效")
    output: list[dict[str, str]] = []
    for raw in parsed:
        if not isinstance(raw, dict):
            raise CommandError("D1 客服消息行无效")
        item = {
            "sender": str(raw.get("sender") or ""),
            "sentAt": str(raw.get("sentAt") or ""),
            "content": str(raw.get("content") or ""),
        }
        if len(item["sender"]) > 120 or len(item["sentAt"]) > 80 or len(item["content"]) > 4_000:
            raise CommandError("D1 客服消息字段超过迁移安全上限")
        output.append(item)
    if len(_json(output).encode("utf-8")) > MIGRATION_MAX_MESSAGE_BYTES:
        raise CommandError("D1 客服消息集合超过迁移字节上限")
    return output


def _source_snapshot(path: Path) -> dict[str, object]:
    try:
        source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise CommandError("无法以只读方式打开 D1 客服快照") from error
    source.row_factory = sqlite3.Row
    try:
        source.execute("BEGIN")
        for name in ("customer_service_import_batches", "customer_service_conversations"):
            if not _table(source, name):
                raise CommandError(f"D1 快照缺少客服迁移表 {name}，或客服域已经退役")
        raw_batches = _rows(source, "SELECT * FROM customer_service_import_batches ORDER BY created_at,id")
        raw_conversations = _rows(source, "SELECT * FROM customer_service_conversations ORDER BY id")
        if any(str(row.get("status") or "") == "processing" for row in raw_batches):
            raise CommandError("D1 仍有 processing 客服导入批次")
        versions = {
            _integer(row["conversation_id"], "会话版本身份", minimum=1): _integer(row["version"], "会话版本", minimum=1)
            for row in _rows(source, "SELECT conversation_id,version FROM customer_service_conversation_versions")
        } if _table(source, "customer_service_conversation_versions") else {}
        raw_fingerprints = _rows(
            source, "SELECT * FROM import_content_fingerprints WHERE domain=? ORDER BY sequence", (DOMAIN,)
        ) if _table(source, "import_content_fingerprints") else []
        raw_attempts = _rows(
            source, "SELECT * FROM import_content_attempts WHERE domain=? ORDER BY sequence", (DOMAIN,)
        ) if _table(source, "import_content_attempts") else []
        raw_heads = _rows(
            source, "SELECT * FROM import_scope_heads WHERE domain=? ORDER BY scope_key", (DOMAIN,)
        ) if _table(source, "import_scope_heads") else []
        raw_audits = _rows(source, "SELECT * FROM customer_service_deletion_audits ORDER BY deleted_at,audit_id") if _table(source, "customer_service_deletion_audits") else []
        source.rollback()
    except sqlite3.DatabaseError as error:
        source.rollback()
        raise CommandError("读取 D1 客服快照失败") from error
    finally:
        source.close()

    batch_rows: dict[str, list[dict[str, object]]] = {}
    for row in raw_conversations:
        batch_rows.setdefault(str(row.get("last_import_batch_id") or ""), []).append(row)
    fingerprints_by_batch = {str(row.get("batch_id") or ""): row for row in raw_fingerprints}
    batches: list[dict[str, object]] = []
    batch_meta: dict[str, dict[str, object]] = {}
    for row in raw_batches:
        batch_id = str(row.get("id") or "").strip()
        shop = str(row.get("shop_name") or "").strip()
        if not batch_id or not shop:
            raise CommandError("D1 客服批次身份不完整")
        owned = batch_rows.get(batch_id, [])
        keys = sorted(str(item.get("conversation_key") or "") for item in owned)
        if any(not key for key in keys) or len(keys) != len(set(keys)):
            raise CommandError("D1 客服批次包含空或重复会话身份")
        fingerprint = fingerprints_by_batch.get(batch_id, {})
        scope_key = _hex(fingerprint.get("scope_key"), {"domain": DOMAIN, "shopName": shop})
        content_hash = _hex(fingerprint.get("content_hash"), {"batchId": batch_id, "conversationKeys": keys})
        import_hash = _hex(fingerprint.get("import_hash"), {"batchId": batch_id, "contentHash": content_hash})
        raw_hash = _hex(row.get("file_hash") or fingerprint.get("raw_file_hash"), {"batchId": batch_id, "files": [row.get("session_file_name"), row.get("chat_file_name")]})
        identity_hash = _sha({"shopName": shop, "conversationKeys": keys})
        state_token = _sha({"scopeKey": scope_key, "batchId": batch_id, "contentHash": content_hash, "rowCount": len(owned)})
        item = {
            "id": batch_id, "shopName": shop,
            "sessionFileName": str(row.get("session_file_name") or "")[:255],
            "chatFileName": str(row.get("chat_file_name") or "")[:255],
            "rawFileHash": raw_hash, "importHash": import_hash, "contentHash": content_hash,
            "identitySetHash": identity_hash, "scopeKey": scope_key,
            "publishedStateToken": state_token, "status": str(row.get("status") or "completed"),
            "conversationCount": _integer(row.get("conversation_count"), "会话总数"),
            "matchedCount": _integer(row.get("matched_count"), "匹配数"),
            "sessionOnlyCount": _integer(row.get("session_only_count"), "咨询单独有数"),
            "chatOnlyCount": _integer(row.get("chat_only_count"), "聊天单独有数"),
            "ambiguousCount": _integer(row.get("ambiguous_count"), "歧义数"),
            "warnings": _warnings(row.get("warnings_json")), "actorEmail": "migration@local",
            "createdAt": _timestamp(row.get("created_at")).isoformat(),
            "completedAt": _optional_timestamp(row.get("completed_at")).isoformat() if row.get("completed_at") else None,
        }
        # Re-imports update a conversation's last_import_batch_id, so an older
        # completed batch can legitimately retain a larger historical count than
        # the rows it currently owns. It must never claim fewer rows than still
        # point at that batch.
        if item["status"] == "completed" and item["conversationCount"] < len(owned):
            raise CommandError("D1 客服批次会话数量小于当前事实所有权")
        batches.append(item)
        batch_meta[batch_id] = item
    if any(str(row.get("last_import_batch_id") or "") not in batch_meta for row in raw_conversations):
        raise CommandError("D1 客服会话存在孤立批次引用")

    conversations: list[dict[str, object]] = []
    for row in raw_conversations:
        identifier = _integer(row.get("id"), "会话 ID", minimum=1)
        conversations.append({
            "id": identifier, "conversationKey": str(row.get("conversation_key") or ""),
            "firstImportBatchId": str(row.get("first_import_batch_id") or ""),
            "lastImportBatchId": str(row.get("last_import_batch_id") or ""),
            "shopName": str(row.get("shop_name") or ""), "consultedAt": str(row.get("consulted_at") or ""),
            "customerId": str(row.get("customer_id") or ""), "customerAlias": str(row.get("customer_alias") or ""),
            "consultationType": str(row.get("consultation_type") or ""), "agent": str(row.get("agent") or ""),
            "transferredAgent": str(row.get("transferred_agent") or ""), "skillGroup": str(row.get("skill_group") or ""),
            "productSku": str(row.get("product_sku") or ""), "productName": str(row.get("product_name") or ""),
            "firstResponseAt": str(row.get("first_response_at") or ""),
            "responseSeconds": str(Decimal(str(row["response_seconds"])).quantize(Decimal("0.001"))) if row.get("response_seconds") is not None else None,
            "durationMinutes": str(Decimal(str(row["duration_minutes"])).quantize(Decimal("0.001"))) if row.get("duration_minutes") is not None else None,
            "customerMessageCount": _integer(row["customer_message_count"], "顾客消息数") if row.get("customer_message_count") is not None else None,
            "agentMessageCount": _integer(row["agent_message_count"], "客服消息数") if row.get("agent_message_count") is not None else None,
            "satisfaction": str(row.get("satisfaction") or ""), "resolved": str(row.get("resolved") or ""),
            "conversationId": str(row.get("conversation_id") or ""), "matchStatus": str(row.get("match_status") or ""),
            "matchConfidence": str(row.get("match_confidence") or ""), "chatStartedAt": str(row.get("chat_started_at") or ""),
            "chatEndedAt": str(row.get("chat_ended_at") or ""), "chatCustomerAlias": str(row.get("chat_customer_alias") or ""),
            "messages": _messages(row.get("messages_json")), "robotScope": str(row.get("robot_scope") or ""),
            "problemType": str(row.get("problem_type") or ""), "conversionStatus": str(row.get("conversion_status") or ""),
            "serviceIssues": str(row.get("service_issues") or ""), "summaryText": str(row.get("summary_text") or ""),
            "analysisSource": str(row.get("analysis_source") or ""),
            "analyzedAt": _optional_timestamp(row.get("analyzed_at")).isoformat() if row.get("analyzed_at") else None,
            "annotatedAt": _optional_timestamp(row.get("annotated_at")).isoformat() if row.get("annotated_at") else None,
            "version": versions.get(identifier, 1), "createdAt": _timestamp(row.get("created_at")).isoformat(),
            "updatedAt": _timestamp(row.get("updated_at")).isoformat(),
        })
    keys = [str(item["conversationKey"]) for item in conversations]
    if len(keys) != len(set(keys)):
        raise CommandError("D1 客服会话业务身份重复")

    audits = [{
        "auditId": str(uuid.UUID(str(row.get("audit_id")))) if re.fullmatch(r"[0-9a-fA-F-]{36}", str(row.get("audit_id") or "")) else str(uuid.uuid5(uuid.NAMESPACE_URL, f"teruisi:{DOMAIN}:audit:{row.get('audit_id')}")),
        "conversationId": _integer(row.get("conversation_id"), "删除审计会话 ID", minimum=1),
        "conversationKey": str(row.get("conversation_key") or ""), "actor": str(row.get("actor") or "")[:320],
        "oldVersion": _integer(row.get("old_version"), "删除前版本", minimum=1),
        "expectedVersion": _integer(row.get("expected_version"), "删除预期版本", minimum=1),
        "reason": str(row.get("reason") or "")[:200], "deletedAt": _timestamp(row.get("deleted_at")).isoformat(),
    } for row in raw_audits]
    attempts = [{
        "id": str(uuid.UUID(str(row.get("attempt_id")))) if re.fullmatch(r"[0-9a-fA-F-]{36}", str(row.get("attempt_id") or "")) else str(uuid.uuid5(uuid.NAMESPACE_URL, f"teruisi:{DOMAIN}:attempt:{row.get('attempt_id')}")),
        "batchId": str(row.get("batch_id") or ""), "scopeKey": _hex(row.get("scope_key"), {"attempt": row.get("attempt_id")}),
        "scope": _parse_json(row.get("scope_json"), {}), "importHash": _hex(row.get("import_hash"), {"attempt": row.get("attempt_id"), "kind": "import"}),
        "rawFileHash": _hex(row.get("raw_file_hash"), {"attempt": row.get("attempt_id"), "kind": "raw"}),
        "contentHash": _hex(row.get("content_hash"), {"attempt": row.get("attempt_id"), "kind": "content"}),
        "rowCount": _integer(row.get("row_count"), "尝试行数"), "fileName": str(row.get("file_name") or "")[:520],
        "fileSizeBytes": _integer(row.get("file_size_bytes"), "尝试文件大小"), "actorEmail": str(row.get("actor") or "")[:320],
        "warnings": _warnings(row.get("warnings_json")), "outcome": str(row.get("outcome") or ""),
        "errorCode": str(row.get("error_code") or "")[:80], "createdAt": _timestamp(row.get("created_at")).isoformat(),
        "updatedAt": _timestamp(row.get("updated_at")).isoformat(),
    } for row in raw_attempts]
    fingerprints = [{
        "batchId": str(row.get("batch_id") or ""), "scopeKey": _hex(row.get("scope_key"), {"fingerprint": row.get("sequence")}),
        "scope": _parse_json(row.get("scope_json"), {}), "importHash": _hex(row.get("import_hash"), {"fingerprint": row.get("sequence"), "kind": "import"}),
        "rawFileHash": _hex(row.get("raw_file_hash"), {"fingerprint": row.get("sequence"), "kind": "raw"}),
        "contentHash": _hex(row.get("content_hash"), {"fingerprint": row.get("sequence"), "kind": "content"}),
        "rowCount": _integer(row.get("row_count"), "指纹行数"), "outcome": str(row.get("status") or "completed"),
        "publishedStateToken": _sha({"publicationSequence": row.get("publication_sequence"), "batchId": row.get("batch_id")}),
        "createdAt": _timestamp(row.get("created_at")).isoformat(),
    } for row in raw_fingerprints]
    scope_shops = {str(item["scopeKey"]): str(item["shopName"]) for item in batches}
    heads = [{
        "scopeKey": _hex(row.get("scope_key"), {"head": row.get("scope_key")}),
        "shopName": scope_shops.get(_hex(row.get("scope_key"), {"head": row.get("scope_key")}), ""),
        "stateToken": _hex(row.get("state_token"), {"head": row.get("scope_key"), "state": row.get("state_token")}),
        "status": str(row.get("status") or "ready"), "ownerToken": str(row.get("owner_token") or ""),
        "generation": _integer(row.get("generation"), "范围 generation"), "currentBatchId": str(row.get("current_batch_id") or ""),
    } for row in raw_heads]
    if any(item["status"] != "ready" or item["ownerToken"] for item in heads):
        raise CommandError("D1 客服导入范围仍处于非静默状态")
    if any(not item["shopName"] for item in heads):
        raise CommandError("D1 客服导入范围无法解析店铺身份")
    if not heads:
        latest_by_scope: dict[str, dict[str, object]] = {}
        for item in batches:
            latest_by_scope[str(item["scopeKey"])] = item
        heads = [{
            "scopeKey": scope, "shopName": str(item["shopName"]), "stateToken": str(item["publishedStateToken"]),
            "status": "ready", "ownerToken": "", "generation": 1, "currentBatchId": str(item["id"]),
        } for scope, item in sorted(latest_by_scope.items())]
    audits.sort(key=lambda item: (str(item["deletedAt"]), str(item["auditId"])))
    attempts.sort(key=lambda item: (str(item["createdAt"]), str(item["id"])))
    fingerprints.sort(key=lambda item: (str(item["createdAt"]), str(item["batchId"])))
    return {
        "version": GENERATION_VERSION, "batches": batches, "conversations": conversations,
        "audits": audits, "attempts": attempts, "fingerprints": fingerprints, "heads": heads,
    }


def _counts(snapshot: dict[str, object]) -> dict[str, int]:
    return {name: len(snapshot[name]) for name in ("batches", "conversations", "audits", "attempts", "fingerprints", "heads")}


def _target_snapshot(generation: str) -> dict[str, object]:
    def dt(value):
        return value.astimezone(datetime_timezone.utc).isoformat() if value else None
    batches = [{
        "id": row.id, "shopName": row.shop_name, "sessionFileName": row.session_file_name,
        "chatFileName": row.chat_file_name, "rawFileHash": row.raw_file_hash, "importHash": row.import_hash,
        "contentHash": row.content_hash, "identitySetHash": row.identity_set_hash, "scopeKey": row.scope_key,
        "publishedStateToken": row.published_state_token, "status": row.status,
        "conversationCount": int(row.conversation_count), "matchedCount": int(row.matched_count),
        "sessionOnlyCount": int(row.session_only_count), "chatOnlyCount": int(row.chat_only_count),
        "ambiguousCount": int(row.ambiguous_count), "warnings": row.warnings_json,
        "actorEmail": row.actor_email, "createdAt": dt(row.created_at), "completedAt": dt(row.completed_at),
    } for row in CustomerServiceImportBatch.objects.filter(migration_generation=generation).order_by("created_at", "id")]
    conversations = [{
        "id": int(row.id), "conversationKey": row.conversation_key, "firstImportBatchId": row.first_import_batch_id,
        "lastImportBatchId": row.last_import_batch_id, "shopName": row.shop_name, "consultedAt": row.consulted_at,
        "customerId": row.customer_id, "customerAlias": row.customer_alias, "consultationType": row.consultation_type,
        "agent": row.agent, "transferredAgent": row.transferred_agent, "skillGroup": row.skill_group,
        "productSku": row.product_sku, "productName": row.product_name, "firstResponseAt": row.first_response_at,
        "responseSeconds": str(row.response_seconds) if row.response_seconds is not None else None,
        "durationMinutes": str(row.duration_minutes) if row.duration_minutes is not None else None,
        "customerMessageCount": row.customer_message_count, "agentMessageCount": row.agent_message_count,
        "satisfaction": row.satisfaction, "resolved": row.resolved, "conversationId": row.conversation_id,
        "matchStatus": row.match_status, "matchConfidence": row.match_confidence, "chatStartedAt": row.chat_started_at,
        "chatEndedAt": row.chat_ended_at, "chatCustomerAlias": row.chat_customer_alias, "messages": row.messages,
        "robotScope": row.robot_scope, "problemType": row.problem_type, "conversionStatus": row.conversion_status,
        "serviceIssues": row.service_issues, "summaryText": row.summary_text, "analysisSource": row.analysis_source,
        "analyzedAt": dt(row.analyzed_at), "annotatedAt": dt(row.annotated_at), "version": int(row.version),
        "createdAt": dt(row.created_at), "updatedAt": dt(row.updated_at),
    } for row in CustomerServiceConversation.objects.filter(migration_generation=generation).order_by("id")]
    audits = [{
        "auditId": str(row.audit_id), "conversationId": int(row.conversation_id), "conversationKey": row.conversation_key,
        "actor": row.actor, "oldVersion": int(row.old_version), "expectedVersion": int(row.expected_version),
        "reason": row.reason, "deletedAt": dt(row.deleted_at),
    } for row in CustomerServiceDeletionAudit.objects.filter(migration_generation=generation).order_by("deleted_at", "audit_id")]
    attempts = [{
        "id": str(row.id), "batchId": row.batch_id, "scopeKey": row.scope_key, "scope": row.scope_json,
        "importHash": row.import_hash, "rawFileHash": row.raw_file_hash, "contentHash": row.content_hash,
        "rowCount": int(row.row_count), "fileName": row.file_name, "fileSizeBytes": int(row.file_size_bytes),
        "actorEmail": row.actor_email, "warnings": row.warnings_json, "outcome": row.outcome,
        "errorCode": row.error_code, "createdAt": dt(row.created_at), "updatedAt": dt(row.updated_at),
    } for row in CustomerServiceImportAttempt.objects.filter(migration_generation=generation).order_by("created_at", "id")]
    fingerprints = [{
        "batchId": row.batch_id, "scopeKey": row.scope_key, "scope": row.scope_json,
        "importHash": row.import_hash, "rawFileHash": row.raw_file_hash, "contentHash": row.content_hash,
        "rowCount": int(row.row_count), "outcome": row.outcome,
        "publishedStateToken": row.published_state_token, "createdAt": dt(row.created_at),
    } for row in CustomerServiceImportFingerprint.objects.filter(migration_generation=generation).order_by("created_at", "batch_id")]
    heads = [{
        "scopeKey": row.scope_key, "shopName": row.shop_name, "stateToken": row.state_token,
        "status": row.status, "ownerToken": row.owner_token, "generation": int(row.generation),
        "currentBatchId": row.current_batch_id,
    } for row in CustomerServiceImportScopeHead.objects.order_by("scope_key")]
    return {"version": GENERATION_VERSION, "batches": batches, "conversations": conversations, "audits": audits, "attempts": attempts, "fingerprints": fingerprints, "heads": heads}


def _assert_empty_target() -> None:
    models = (CustomerServiceImportBatch, CustomerServiceConversation, CustomerServiceDeletionAudit, CustomerServiceImportAttempt, CustomerServiceImportFingerprint, CustomerServiceImportScopeHead)
    if any(model.objects.exists() for model in models):
        raise CommandError("PostgreSQL 客服目标不是空域，拒绝覆盖或合并迁移")
    authority = CustomerServiceWriteAuthority.objects.select_for_update().filter(id=1).first()
    if authority is None or authority.status != "d1" or authority.authority_epoch is not None or authority.cutover_id:
        raise CommandError("PostgreSQL 客服 authority 不处于未切换的 d1 状态")


def _apply(snapshot: dict[str, object], generation: str) -> None:
    batches = snapshot["batches"]
    conversations = snapshot["conversations"]
    audits = snapshot["audits"]
    attempts = snapshot["attempts"]
    fingerprints = snapshot["fingerprints"]
    heads = snapshot["heads"]
    CustomerServiceImportBatch.objects.bulk_create([CustomerServiceImportBatch(
        id=item["id"], shop_name=item["shopName"], session_file_name=item["sessionFileName"], chat_file_name=item["chatFileName"],
        raw_file_hash=item["rawFileHash"], import_hash=item["importHash"], content_hash=item["contentHash"],
        identity_set_hash=item["identitySetHash"], scope_key=item["scopeKey"], published_state_token=item["publishedStateToken"],
        status=item["status"], conversation_count=item["conversationCount"], matched_count=item["matchedCount"],
        session_only_count=item["sessionOnlyCount"], chat_only_count=item["chatOnlyCount"], ambiguous_count=item["ambiguousCount"],
        warnings_json=item["warnings"], actor_email=item["actorEmail"], created_at=_timestamp(item["createdAt"]),
        completed_at=_optional_timestamp(item["completedAt"]), migration_generation=generation,
    ) for item in batches])
    for item in batches:
        CustomerServiceImportBatch.objects.filter(id=item["id"]).update(
            created_at=_timestamp(item["createdAt"]), completed_at=_optional_timestamp(item["completedAt"])
        )
    CustomerServiceConversation.objects.bulk_create([CustomerServiceConversation(
        id=item["id"], conversation_key=item["conversationKey"], first_import_batch_id=item["firstImportBatchId"],
        last_import_batch_id=item["lastImportBatchId"], shop_name=item["shopName"], consulted_at=item["consultedAt"],
        customer_id=item["customerId"], customer_alias=item["customerAlias"], consultation_type=item["consultationType"], agent=item["agent"],
        transferred_agent=item["transferredAgent"], skill_group=item["skillGroup"], product_sku=item["productSku"], product_name=item["productName"],
        first_response_at=item["firstResponseAt"], response_seconds=Decimal(item["responseSeconds"]) if item["responseSeconds"] is not None else None,
        duration_minutes=Decimal(item["durationMinutes"]) if item["durationMinutes"] is not None else None,
        customer_message_count=item["customerMessageCount"], agent_message_count=item["agentMessageCount"], satisfaction=item["satisfaction"],
        resolved=item["resolved"], conversation_id=item["conversationId"], match_status=item["matchStatus"], match_confidence=item["matchConfidence"],
        chat_started_at=item["chatStartedAt"], chat_ended_at=item["chatEndedAt"], chat_customer_alias=item["chatCustomerAlias"], messages=item["messages"],
        robot_scope=item["robotScope"], problem_type=item["problemType"], conversion_status=item["conversionStatus"], service_issues=item["serviceIssues"],
        summary_text=item["summaryText"], analysis_source=item["analysisSource"], analyzed_at=_optional_timestamp(item["analyzedAt"]),
        annotated_at=_optional_timestamp(item["annotatedAt"]), version=item["version"], created_at=_timestamp(item["createdAt"]),
        updated_at=_timestamp(item["updatedAt"]), migration_generation=generation,
    ) for item in conversations])
    for item in conversations:
        CustomerServiceConversation.objects.filter(id=item["id"]).update(
            created_at=_timestamp(item["createdAt"]), updated_at=_timestamp(item["updatedAt"])
        )
    CustomerServiceDeletionAudit.objects.bulk_create([CustomerServiceDeletionAudit(
        audit_id=item["auditId"], conversation_id=item["conversationId"], conversation_key=item["conversationKey"], actor=item["actor"],
        old_version=item["oldVersion"], expected_version=item["expectedVersion"], reason=item["reason"], deleted_at=_timestamp(item["deletedAt"]),
        migration_generation=generation,
    ) for item in audits])
    for item in audits:
        CustomerServiceDeletionAudit.objects.filter(audit_id=item["auditId"]).update(
            deleted_at=_timestamp(item["deletedAt"])
        )
    CustomerServiceImportAttempt.objects.bulk_create([CustomerServiceImportAttempt(
        id=item["id"], batch_id=item["batchId"], scope_key=item["scopeKey"], scope_json=item["scope"], import_hash=item["importHash"],
        raw_file_hash=item["rawFileHash"], content_hash=item["contentHash"], row_count=item["rowCount"], file_name=item["fileName"],
        file_size_bytes=item["fileSizeBytes"], actor_email=item["actorEmail"], warnings_json=item["warnings"], outcome=item["outcome"],
        error_code=item["errorCode"], created_at=_timestamp(item["createdAt"]), updated_at=_timestamp(item["updatedAt"]), migration_generation=generation,
    ) for item in attempts])
    for item in attempts:
        CustomerServiceImportAttempt.objects.filter(id=item["id"]).update(
            created_at=_timestamp(item["createdAt"]), updated_at=_timestamp(item["updatedAt"])
        )
    CustomerServiceImportFingerprint.objects.bulk_create([CustomerServiceImportFingerprint(
        batch_id=item["batchId"], scope_key=item["scopeKey"], scope_json=item["scope"], import_hash=item["importHash"], raw_file_hash=item["rawFileHash"],
        content_hash=item["contentHash"], row_count=item["rowCount"], outcome=item["outcome"], published_state_token=item["publishedStateToken"],
        created_at=_timestamp(item["createdAt"]), migration_generation=generation,
    ) for item in fingerprints])
    for item in fingerprints:
        CustomerServiceImportFingerprint.objects.filter(
            domain=DOMAIN, batch_id=item["batchId"]
        ).update(created_at=_timestamp(item["createdAt"]))
    CustomerServiceImportScopeHead.objects.bulk_create([CustomerServiceImportScopeHead(
        scope_key=item["scopeKey"], shop_name=item["shopName"], state_token=item["stateToken"], status=item["status"],
        owner_token=item["ownerToken"], generation=item["generation"], current_batch_id=item["currentBatchId"],
    ) for item in heads])


class Command(BaseCommand):
    help = "Plan, apply, and verify the terminal D1-to-PostgreSQL customer-service migration."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--source", required=True)
        action = parser.add_mutually_exclusive_group(required=True)
        action.add_argument("--plan", action="store_true")
        action.add_argument("--apply", action="store_true")
        action.add_argument("--verify", action="store_true")
        parser.add_argument("--approved-plan-id", default="")
        parser.add_argument("--approved-run-id", default="")

    def handle(self, *args, **options) -> None:
        if settings.DJANGO_ENVIRONMENT == "production" and settings.DJANGO_PROCESS_ROLE != "migration_writer":
            raise CommandError("生产客服迁移只能由 migration_writer 进程角色执行")
        source_input = Path(str(options["source"])).expanduser()
        if (
            not source_input.is_file()
            or source_input.is_symlink()
            or source_input.suffix.lower() not in {".sqlite", ".sqlite3"}
        ):
            raise CommandError("D1 客服源必须是普通 SQLite 文件")
        path = source_input.resolve()
        snapshot = _source_snapshot(path)
        digest = _sha(snapshot)
        counts = _counts(snapshot)
        path_digest = hashlib.sha256(str(path).lower().encode("utf-8")).hexdigest()
        if options["plan"]:
            plan_id = f"customer-service-plan-{uuid.uuid4().hex}"
            CustomerServiceMigrationRun.objects.create(
                id=plan_id, mode="plan", status="planned", source_snapshot_digest=digest,
                source_counts=counts, manifest={"version": GENERATION_VERSION, "sourcePathDigest": path_digest, "sourceDigest": digest},
            )
            self.stdout.write(_json({"status": "planned", "planId": plan_id, "sourceDigest": digest, "counts": counts}))
            return
        if options["apply"]:
            approved = str(options.get("approved_plan_id") or "").strip()
            plan = CustomerServiceMigrationRun.objects.filter(id=approved, mode="plan", status="planned").first()
            if plan is None or plan.source_snapshot_digest != digest or plan.source_counts != counts or plan.manifest.get("sourcePathDigest") != path_digest:
                raise CommandError("approved-plan-id 未绑定当前冻结客服快照")
            run_id = f"customer-service-{uuid.uuid4().hex}"
            with transaction.atomic():
                _assert_empty_target()
                _apply(snapshot, run_id)
                target = _target_snapshot(run_id)
                target_digest = _sha(target)
                if target_digest != digest or _counts(target) != counts:
                    raise CommandError("客服迁移落库回查与源快照不一致")
                revision = CustomerServiceDataRevision.objects.select_for_update().get(domain=DOMAIN)
                revision.revision = 1
                revision.source_digest = digest
                revision.save()
                run = CustomerServiceMigrationRun.objects.create(
                    id=run_id, mode="apply", status="verified", source_snapshot_digest=digest,
                    target_snapshot_digest=target_digest, source_counts=counts, target_counts=counts,
                    manifest={"version": GENERATION_VERSION, "sourcePathDigest": path_digest, "sourceDigest": digest},
                    approved_run_id=approved, completed_at=timezone.now(),
                )
                authority = CustomerServiceWriteAuthority.objects.select_for_update().get(id=1)
                authority.migration_verify_run_id = run.id
                authority.save(update_fields=["migration_verify_run_id", "updated_at"])
                plan.status = "consumed"
                plan.approved_run_id = run.id
                plan.completed_at = timezone.now()
                plan.save()
            self.stdout.write(_json({"status": "verified", "runId": run_id, "sourceDigest": digest, "counts": counts}))
            return
        approved = str(options.get("approved_run_id") or "").strip()
        run = CustomerServiceMigrationRun.objects.filter(id=approved, mode="apply", status="verified").first()
        if run is None or run.source_snapshot_digest != digest or run.source_counts != counts or run.manifest.get("sourcePathDigest") != path_digest:
            raise CommandError("approved-run-id 未绑定当前冻结客服快照")
        target = _target_snapshot(run.id)
        if _sha(target) != digest or _counts(target) != counts or run.target_snapshot_digest != digest:
            raise CommandError("PostgreSQL 客服迁移复验失败")
        self.stdout.write(_json({"status": "verified", "runId": run.id, "sourceDigest": digest, "counts": counts}))

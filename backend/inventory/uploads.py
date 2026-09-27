from __future__ import annotations

from datetime import timedelta
import hashlib
import math
import secrets
import uuid

from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from .errors import InventoryApiError
from .models import InventoryRawUploadChunk, InventoryRawUploadSession
from .write_requests import lock_active_authority


CHUNK_SIZE_BYTES = 1024 * 1024
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
MAX_CHUNKS = MAX_FILE_SIZE_BYTES // CHUNK_SIZE_BYTES
UPLOAD_TTL = timedelta(hours=6)
PROCESSING_STALE_AGE = timedelta(minutes=30)


def _error(message: str, *, code: str = "invalid_request", status: int = 400) -> InventoryApiError:
    return InventoryApiError(message, code=code, status=status)


def _text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise _error(f"{label} 无效")
    return value.strip()


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise _error(f"{label} 无效")
    return value


def _session_payload(session: InventoryRawUploadSession) -> dict[str, object]:
    return {
        "id": str(session.id),
        "dataset": session.dataset,
        "snapshotDate": session.snapshot_date.isoformat() if session.snapshot_date else None,
        "fileName": session.file_name,
        "fileSizeBytes": int(session.file_size_bytes),
        "chunkSizeBytes": int(session.chunk_size_bytes),
        "chunkCount": int(session.chunk_count),
        "receivedChunkCount": int(session.received_chunk_count),
        "receivedBytes": int(session.received_bytes),
        "status": session.status,
        "ownerGeneration": int(session.owner_generation),
        "expiresAt": session.expires_at.isoformat(),
    }


def _locked_session(upload_id: object, actor_email: str) -> InventoryRawUploadSession:
    try:
        parsed = uuid.UUID(str(upload_id))
    except (ValueError, TypeError, AttributeError) as error:
        raise _error("上传会话标识无效") from error
    session = (
        InventoryRawUploadSession.objects.select_for_update()
        .filter(id=parsed, actor_email=actor_email[:320])
        .first()
    )
    if session is None:
        raise _error("上传会话不存在", code="not_found", status=404)
    if session.expires_at <= timezone.now() and session.status != "completed":
        raise _error("上传会话已过期", code="conflict", status=409)
    return session


def cleanup_expired() -> None:
    ids = list(
        InventoryRawUploadSession.objects.filter(expires_at__lte=timezone.now())
        .exclude(status="processing")
        .order_by("expires_at")
        .values_list("id", flat=True)[:20]
    )
    if ids:
        InventoryRawUploadSession.objects.filter(id__in=ids).delete()


def begin_upload(payload: dict[str, object], actor_email: str) -> dict[str, object]:
    if set(payload) != {"action", "dataset", "snapshotDate", "fileName", "fileSizeBytes", "chunkCount", "fingerprint"}:
        raise _error("上传初始化字段集合无效")
    dataset = payload["dataset"]
    if dataset not in {"stock", "age"}:
        raise _error("dataset 无效")
    snapshot = _text(payload["snapshotDate"], "snapshotDate", 10)
    try:
        snapshot_date = timezone.datetime.fromisoformat(snapshot).date()
    except ValueError as error:
        raise _error("snapshotDate 无效") from error
    if snapshot_date.isoformat() != snapshot:
        raise _error("snapshotDate 无效")
    file_name = _text(payload["fileName"], "fileName", 255)
    file_size = _integer(payload["fileSizeBytes"], "fileSizeBytes", 1, MAX_FILE_SIZE_BYTES)
    chunk_count = _integer(payload["chunkCount"], "chunkCount", 1, MAX_CHUNKS)
    if chunk_count != math.ceil(file_size / CHUNK_SIZE_BYTES):
        raise _error("chunkCount 与文件大小不一致")
    fingerprint = _text(payload["fingerprint"], "fingerprint", 200)
    normalized_fingerprint = f"inventory:{dataset}:{snapshot}:{fingerprint}"
    with transaction.atomic():
        lock_active_authority()
        cleanup_expired()
        session = (
            InventoryRawUploadSession.objects.select_for_update()
            .filter(
                fingerprint=normalized_fingerprint,
                actor_email=actor_email[:320],
                expires_at__gt=timezone.now(),
                status__in=["uploading", "ready", "processing"],
            )
            .order_by("-created_at")
            .first()
        )
        if session:
            if session.file_name != file_name or int(session.file_size_bytes) != file_size or int(session.chunk_count) != chunk_count:
                raise _error("上传指纹已绑定另一份文件", code="version_conflict", status=409)
            return {"upload": _session_payload(session)}
        session = InventoryRawUploadSession.objects.create(
            fingerprint=normalized_fingerprint,
            dataset=str(dataset),
            snapshot_date=snapshot_date,
            actor_email=actor_email[:320],
            file_name=file_name,
            file_size_bytes=file_size,
            chunk_size_bytes=CHUNK_SIZE_BYTES,
            chunk_count=chunk_count,
            expires_at=timezone.now() + UPLOAD_TTL,
        )
        return {"upload": _session_payload(session)}


def receive_chunk(upload_id: str, chunk_index: int, payload: bytes, actor_email: str) -> dict[str, object]:
    if not payload:
        raise _error("上传分片为空")
    with transaction.atomic():
        lock_active_authority()
        session = _locked_session(upload_id, actor_email)
        if session.status not in {"uploading", "ready"}:
            raise _error("文件已开始处理，不能继续覆盖分片", code="conflict", status=409)
        index = _integer(chunk_index, "chunkIndex", 0, int(session.chunk_count) - 1)
        expected = int(session.file_size_bytes) - int(session.chunk_size_bytes) * (int(session.chunk_count) - 1) if index == int(session.chunk_count) - 1 else int(session.chunk_size_bytes)
        if len(payload) != expected:
            raise _error("分片大小与预期不一致", status=422)
        digest = hashlib.sha256(payload).hexdigest()
        InventoryRawUploadChunk.objects.update_or_create(
            session=session,
            chunk_index=index,
            defaults={"object_key": f"inventory-upload/{session.id}/{index:06d}-{digest}", "size_bytes": len(payload), "sha256": digest, "payload": payload},
        )
        aggregate = session.chunks.aggregate(count=Count("id"), total=Sum("size_bytes"))
        session.received_chunk_count = int(aggregate["count"] or 0)
        session.received_bytes = int(aggregate["total"] or 0)
        session.status = "ready" if session.received_chunk_count == int(session.chunk_count) else "uploading"
        session.expires_at = timezone.now() + UPLOAD_TTL
        session.save()
        return {"upload": _session_payload(session)}


def _owner(session: InventoryRawUploadSession, token: object) -> None:
    if not isinstance(token, str) or not token or not secrets.compare_digest(session.owner_token, token):
        raise _error("上传会话处理所有权已失效", code="version_conflict", status=409)


def claim_upload(payload: dict[str, object], actor_email: str) -> dict[str, object]:
    if set(payload) != {"action", "uploadId"}:
        raise _error("上传接管字段集合无效")
    with transaction.atomic():
        lock_active_authority()
        session = _locked_session(payload["uploadId"], actor_email)
        if session.status == "completed":
            return {"kind": "completed", "upload": _session_payload(session), "result": session.result_payload}
        if session.status == "uploading":
            raise _error("仍有分片尚未上传完成", code="conflict", status=409)
        if session.status == "processing" and session.updated_at > timezone.now() - PROCESSING_STALE_AGE:
            return {"kind": "claimed", "ownerToken": session.owner_token, "upload": _session_payload(session), "chunks": list(session.chunks.order_by("chunk_index").values("chunk_index", "size_bytes", "sha256"))}
        if session.status not in {"ready", "processing"} or session.chunks.count() != int(session.chunk_count) or int(session.received_bytes) != int(session.file_size_bytes):
            raise _error("上传分片集合不完整", code="conflict", status=409)
        owner_token = secrets.token_urlsafe(32)
        session.status = "processing"
        session.owner_token = owner_token
        session.owner_generation = int(session.owner_generation) + 1
        session.expires_at = timezone.now() + UPLOAD_TTL
        session.save()
        return {"kind": "claimed", "ownerToken": owner_token, "upload": _session_payload(session), "chunks": list(session.chunks.order_by("chunk_index").values("chunk_index", "size_bytes", "sha256"))}


def read_chunk(upload_id: str, chunk_index: int, owner_token: str, actor_email: str) -> tuple[bytes, str]:
    with transaction.atomic():
        lock_active_authority()
        session = _locked_session(upload_id, actor_email)
        if session.status != "processing":
            raise _error("上传会话尚未进入处理状态", code="conflict", status=409)
        _owner(session, owner_token)
        chunk = session.chunks.filter(chunk_index=chunk_index).first()
        if chunk is None:
            raise _error("上传分片不存在", code="not_found", status=404)
        payload = bytes(chunk.payload)
        digest = hashlib.sha256(payload).hexdigest()
        if len(payload) != int(chunk.size_bytes) or not secrets.compare_digest(digest, chunk.sha256):
            raise _error("上传分片完整性校验失败", status=422)
        return payload, digest


def finish_upload(payload: dict[str, object], actor_email: str) -> dict[str, object]:
    if set(payload) != {"action", "uploadId", "ownerToken", "result"} or not isinstance(payload["result"], dict):
        raise _error("上传完成字段集合无效")
    with transaction.atomic():
        lock_active_authority()
        session = _locked_session(payload["uploadId"], actor_email)
        if session.status == "completed":
            if session.result_payload != payload["result"]:
                raise _error("已完成上传的结果不一致", code="version_conflict", status=409)
            return {"result": session.result_payload}
        if session.status != "processing":
            raise _error("上传会话尚未进入处理状态", code="conflict", status=409)
        _owner(session, payload["ownerToken"])
        session.status = "completed"
        session.result_payload = payload["result"]
        batch = payload["result"].get("batch")
        session.result_batch_id = str(batch.get("id") or "")[:128] if isinstance(batch, dict) else ""
        session.owner_token = ""
        session.expires_at = timezone.now() + UPLOAD_TTL
        session.save()
        session.chunks.all().delete()
        return {"result": session.result_payload}


def release_upload(payload: dict[str, object], actor_email: str) -> dict[str, object]:
    if set(payload) != {"action", "uploadId", "ownerToken"}:
        raise _error("上传释放字段集合无效")
    with transaction.atomic():
        lock_active_authority()
        session = _locked_session(payload["uploadId"], actor_email)
        if session.status == "processing":
            _owner(session, payload["ownerToken"])
            session.status = "ready"
            session.owner_token = ""
            session.expires_at = timezone.now() + UPLOAD_TTL
            session.save()
        return {"upload": _session_payload(session)}


def execute_upload_action(payload: dict[str, object], actor_email: str) -> dict[str, object]:
    action = payload.get("action")
    if action == "init":
        return begin_upload(payload, actor_email)
    if action == "claim":
        return claim_upload(payload, actor_email)
    if action == "finish":
        return finish_upload(payload, actor_email)
    if action == "release":
        return release_upload(payload, actor_email)
    raise _error("未知的分片上传操作")

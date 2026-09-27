from __future__ import annotations

import hashlib
import re
import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .errors import NetshopApiError
from .import_service import assert_active_authority
from .models import NetshopAssetUpload, NetshopAssetUploadChunk, NetshopAssetUploadResult


CHUNK_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
UPLOAD_TTL = timedelta(hours=24)
OBJECT_PREFIX = "netshop-asset-upload/v1"
HEX_64_RE = re.compile(r"^[a-f0-9]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _error(message: str, *, status: int = 400, code: str = "invalid_request") -> None:
    raise NetshopApiError(message, status=status, code=code)


def _text(value: object, label: str, maximum: int, *, required: bool = True) -> str:
    if not isinstance(value, str):
        _error(f"{label}必须是字符串")
    normalized = value.strip()
    if (required and not normalized) or len(normalized) > maximum or "\x00" in normalized:
        _error(f"{label}无效")
    return normalized


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _error(f"{label}无效")
    return value


def _safe_file_name(value: object) -> str:
    name = _text(value, "文件名", 1_000).replace("\\", "/").split("/")[-1]
    name = "".join(char for char in name if ord(char) >= 32 and ord(char) != 127)[:255]
    if not name.lower().endswith(".xlsx"):
        _error("天猫 SPU 商品图只接受 .xlsx 文件", status=422)
    return name


def _expires_at():
    return timezone.now() + UPLOAD_TTL


def _fingerprint(shop_name: str, snapshot_date: str, client_fingerprint: str) -> str:
    value = f"tmall-product-assets\x00{shop_name}\x00{snapshot_date}\x00{client_fingerprint}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _chunks(upload_id: str) -> list[NetshopAssetUploadChunk]:
    return list(
        NetshopAssetUploadChunk.objects.filter(upload_id=upload_id).order_by("chunk_index")
    )


def _session(upload: NetshopAssetUpload, chunks: list[NetshopAssetUploadChunk]) -> dict[str, object]:
    return {
        "id": upload.id,
        "shopName": upload.shop_name,
        "snapshotDate": upload.snapshot_date,
        "fileName": upload.file_name,
        "fileSizeBytes": upload.file_size_bytes,
        "chunkSizeBytes": upload.chunk_size_bytes,
        "chunkCount": upload.chunk_count,
        "receivedChunkIndexes": [chunk.chunk_index for chunk in chunks],
        "receivedBytes": upload.received_bytes,
        "status": upload.status,
        "expiresAt": upload.expires_at.isoformat(),
    }


def _chunk_payload(chunk: NetshopAssetUploadChunk) -> dict[str, object]:
    return {
        "chunkIndex": chunk.chunk_index,
        "objectKey": chunk.object_key,
        "sizeBytes": chunk.size_bytes,
        "sha256": chunk.sha256,
    }


def _locked_upload(upload_id: object) -> NetshopAssetUpload:
    normalized = _text(upload_id, "上传会话标识", 64)
    upload = NetshopAssetUpload.objects.select_for_update().filter(id=normalized).first()
    if upload is None or upload.expires_at <= timezone.now():
        _error("上传会话已过期，请重新选择文件", status=404, code="not_found")
    return upload


def _init(payload: dict[str, object]) -> dict[str, object]:
    allowed = {
        "action", "fileName", "fileSizeBytes", "chunkCount", "clientFingerprint",
        "shopName", "snapshotDate",
    }
    if set(payload) != allowed:
        _error("上传初始化字段与契约不一致")
    file_name = _safe_file_name(payload["fileName"])
    file_size = _integer(payload["fileSizeBytes"], "文件大小", 1, MAX_FILE_BYTES)
    chunk_count = _integer(payload["chunkCount"], "分片数量", 1, 32)
    if chunk_count != (file_size + CHUNK_BYTES - 1) // CHUNK_BYTES:
        _error("分片数量与文件大小不一致")
    client_fingerprint = _text(payload["clientFingerprint"], "上传指纹", 512)
    shop_name = _text(payload["shopName"], "店铺", 100)
    snapshot_date = _text(payload["snapshotDate"], "快照日期", 10)
    if not DATE_RE.fullmatch(snapshot_date):
        _error("上传会话缺少有效快照日期")
    digest = _fingerprint(shop_name, snapshot_date, client_fingerprint)
    existing = NetshopAssetUpload.objects.select_for_update().filter(fingerprint=digest).first()
    cleanup_keys: list[str] = []
    if existing and existing.expires_at > timezone.now() and existing.status in {
        "uploading", "ready", "processing"
    }:
        if (
            existing.shop_name != shop_name
            or existing.snapshot_date != snapshot_date
            or existing.file_size_bytes != file_size
            or existing.file_name != file_name
        ):
            _error("上传指纹已绑定其他文件、店铺或快照日期", status=409, code="conflict")
        chunks = _chunks(existing.id)
        return {"upload": _session(existing, chunks), "cleanupObjectKeys": cleanup_keys}
    if existing:
        cleanup_keys = [chunk.object_key for chunk in _chunks(existing.id)]
        NetshopAssetUploadChunk.objects.filter(upload_id=existing.id).delete()
        NetshopAssetUploadResult.objects.filter(upload_id=existing.id).delete()
        existing.delete()
    upload = NetshopAssetUpload.objects.create(
        id=str(uuid.uuid4()),
        fingerprint=digest,
        shop_name=shop_name,
        snapshot_date=snapshot_date,
        file_name=file_name,
        file_size_bytes=file_size,
        chunk_size_bytes=CHUNK_BYTES,
        chunk_count=chunk_count,
        expires_at=_expires_at(),
    )
    return {"upload": _session(upload, []), "cleanupObjectKeys": cleanup_keys}


def _record_chunk(payload: dict[str, object]) -> dict[str, object]:
    allowed = {"action", "uploadId", "chunkIndex", "objectKey", "sizeBytes", "sha256"}
    if set(payload) != allowed:
        _error("分片登记字段与契约不一致")
    upload = _locked_upload(payload["uploadId"])
    if upload.status not in {"uploading", "ready"}:
        _error("上传会话已进入处理或完成状态", status=409, code="conflict")
    index = _integer(payload["chunkIndex"], "分片序号", 0, upload.chunk_count - 1)
    size = _integer(payload["sizeBytes"], "分片大小", 1, CHUNK_BYTES)
    expected_size = (
        upload.file_size_bytes - upload.chunk_size_bytes * (upload.chunk_count - 1)
        if index == upload.chunk_count - 1
        else upload.chunk_size_bytes
    )
    if size != expected_size:
        _error("分片大小与预期不一致", status=422)
    sha256 = _text(payload["sha256"], "分片哈希", 64).lower()
    if not HEX_64_RE.fullmatch(sha256):
        _error("分片哈希无效")
    object_key = _text(payload["objectKey"], "分片对象键", 1_000)
    expected_key = f"{OBJECT_PREFIX}/{upload.id}/{index:06d}-{sha256}"
    if object_key != expected_key:
        _error("分片对象键与内容寻址契约不一致", status=422)
    previous = NetshopAssetUploadChunk.objects.filter(
        upload_id=upload.id, chunk_index=index
    ).first()
    NetshopAssetUploadChunk.objects.update_or_create(
        upload_id=upload.id,
        chunk_index=index,
        defaults={"object_key": object_key, "size_bytes": size, "sha256": sha256},
    )
    chunks = _chunks(upload.id)
    upload.received_chunk_count = len(chunks)
    upload.received_bytes = sum(chunk.size_bytes for chunk in chunks)
    upload.status = "ready" if len(chunks) == upload.chunk_count else "uploading"
    upload.expires_at = _expires_at()
    upload.save(
        update_fields=[
            "received_chunk_count", "received_bytes", "status", "expires_at", "updated_at"
        ]
    )
    return {
        "upload": _session(upload, chunks),
        "previousObjectKey": previous.object_key if previous and previous.object_key != object_key else None,
    }


def _claim(payload: dict[str, object]) -> dict[str, object]:
    if set(payload) != {"action", "uploadId"}:
        _error("上传接管字段与契约不一致")
    upload = _locked_upload(payload["uploadId"])
    chunks = _chunks(upload.id)
    if upload.status == "completed":
        stored = NetshopAssetUploadResult.objects.filter(upload_id=upload.id).first()
        if stored is None:
            _error("已完成上传会话缺少导入结果", status=503, code="service_unavailable")
        return {"kind": "completed", "session": _session(upload, chunks), "result": stored.result_json}
    if upload.status == "uploading":
        _error("仍有分片尚未上传完成", status=409, code="conflict")
    if upload.status == "processing":
        _error("文件正在合并导入，请稍后重试", status=409, code="conflict")
    if upload.status != "ready" or len(chunks) != upload.chunk_count:
        _error("上传会话状态无效", status=409, code="conflict")
    owner = str(uuid.uuid4())
    upload.status = "processing"
    upload.processing_owner = owner
    upload.owner_generation += 1
    upload.expires_at = _expires_at()
    upload.save(
        update_fields=[
            "status", "processing_owner", "owner_generation", "expires_at", "updated_at"
        ]
    )
    return {
        "kind": "claimed",
        "ownerToken": owner,
        "ownerGeneration": upload.owner_generation,
        "session": _session(upload, chunks),
        "chunks": [_chunk_payload(chunk) for chunk in chunks],
    }


def _complete(payload: dict[str, object]) -> dict[str, object]:
    if set(payload) != {"action", "uploadId", "ownerToken", "ownerGeneration", "result"}:
        _error("上传完成字段与契约不一致")
    upload = _locked_upload(payload["uploadId"])
    owner = _text(payload["ownerToken"], "上传所有者", 64)
    generation = _integer(payload["ownerGeneration"], "上传所有者代次", 1, 1_000_000_000)
    result = payload["result"]
    if not isinstance(result, dict):
        _error("导入结果必须是 JSON 对象")
    if (
        upload.status != "processing"
        or upload.processing_owner != owner
        or upload.owner_generation != generation
    ):
        _error("上传会话提交所有权已变化", status=409, code="version_conflict")
    NetshopAssetUploadResult.objects.update_or_create(
        upload_id=upload.id, defaults={"result_json": result}
    )
    chunks = _chunks(upload.id)
    upload.status = "completed"
    upload.processing_owner = ""
    upload.save(update_fields=["status", "processing_owner", "updated_at"])
    return {
        "upload": _session(upload, chunks),
        "objectKeys": [chunk.object_key for chunk in chunks],
    }


def _release(payload: dict[str, object]) -> dict[str, object]:
    if set(payload) != {"action", "uploadId", "ownerToken", "ownerGeneration"}:
        _error("上传释放字段与契约不一致")
    upload = _locked_upload(payload["uploadId"])
    owner = _text(payload["ownerToken"], "上传所有者", 64)
    generation = _integer(payload["ownerGeneration"], "上传所有者代次", 1, 1_000_000_000)
    if (
        upload.status == "processing"
        and upload.processing_owner == owner
        and upload.owner_generation == generation
    ):
        upload.status = "ready"
        upload.processing_owner = ""
        upload.expires_at = _expires_at()
        upload.save(
            update_fields=["status", "processing_owner", "expires_at", "updated_at"]
        )
    return {"upload": _session(upload, _chunks(upload.id))}


def _prune(payload: dict[str, object]) -> dict[str, object]:
    if set(payload) != {"action", "uploadId", "objectKeys"}:
        _error("分片清理字段与契约不一致")
    upload = _locked_upload(payload["uploadId"])
    if upload.status != "completed":
        _error("只有已完成上传可以清理分片元数据", status=409, code="conflict")
    values = payload["objectKeys"]
    if not isinstance(values, list) or len(values) > 32 or any(not isinstance(item, str) for item in values):
        _error("分片对象清单无效")
    actual = [chunk.object_key for chunk in _chunks(upload.id)]
    if values != actual:
        _error("分片对象清单与会话不一致", status=409, code="version_conflict")
    deleted, _ = NetshopAssetUploadChunk.objects.filter(upload_id=upload.id).delete()
    return {"pruned": deleted, "upload": _session(upload, [])}


def execute_asset_upload_action(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        _error("上传会话请求必须是 JSON 对象")
    action = payload.get("action")
    handlers = {
        "init": _init,
        "record_chunk": _record_chunk,
        "claim": _claim,
        "complete": _complete,
        "release": _release,
        "prune": _prune,
    }
    if action not in handlers:
        _error("未知的上传会话操作")
    with transaction.atomic():
        assert_active_authority()
        return handlers[action](payload)  # type: ignore[index]

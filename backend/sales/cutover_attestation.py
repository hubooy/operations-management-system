"""Durable, self-verifying proof for the cross-database sales cutover."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import UTC
from pathlib import Path
from typing import Any

from django.db import transaction
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .authority_lock import acquire_sales_write_authority_exclusive_lock
from .models import (
    SalesCutoverAttestation,
    SalesDataRevision,
    SalesLegacyUploadAudit,
    SalesMigrationRun,
    SalesWriteAuthority,
)


ATTESTATION_VERSION = "sales-cutover-attestation-v2"
CLEANUP_MANIFEST_VERSION = "sales-legacy-r2-cleanup-v1"
ATTESTED_MIGRATION_FORMAT_VERSION = "sales-projection-v4"
LEGACY_MANIFEST_FORMAT_VERSION = "legacy-sales-upload-manifest-v1"
CUTOVER_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^[0-9a-f]{32,64}$")
EXPECTED_D1_AUTHORITY_TRIGGER_COUNT = 36
EXPECTED_D1_AUTHORITY_SCHEMA_SHA256 = (
    "8a0896d9f6b20c2b39eae8cbf1ab39faa21d4cb772e8990d40902e8b86d8af17"
)
BLOCKER_KEYS = {
    "processingBatches",
    "activeUploads",
    "uploadChunks",
    "processingFingerprints",
    "processingAttempts",
    "processingScopeHeads",
}
TARGET_SNAPSHOT_KEYS = {
    "sales_import_batches",
    "erp_product_master",
    "sales_order_lines",
    "sales_query_projection",
    "import_content_fingerprints",
    "import_content_attempts",
    "import_scope_heads",
    "sales_import_uploads",
    "sales_import_upload_chunks",
}
CLEANUP_CORE_TABLES = {
    "sales_order_lines",
    "sales_import_batches",
    "sales_overview_cache_state",
    "sales_projection_source_state",
    "sales_overview_response_cache",
    "sales_projection_outbox",
    "import_content_fingerprints",
    "import_content_attempts",
    "import_scope_heads",
}
CLEANUP_CORE_SPECS = (
    ("sales_order_lines", ""),
    ("sales_import_batches", ""),
    ("sales_overview_cache_state", "WHERE id = 1"),
    ("sales_projection_source_state", "WHERE id = 1"),
    ("sales_overview_response_cache", ""),
    ("sales_projection_outbox", "WHERE domain = 'sales'"),
    ("import_content_fingerprints", "WHERE domain = 'sales'"),
    ("import_content_attempts", "WHERE domain = 'sales'"),
    ("import_scope_heads", "WHERE domain = 'sales'"),
)


class SalesCutoverAttestationError(RuntimeError):
    pass


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _payload_sha256(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _legacy_manifest_digest(upload_id: str, chunks: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    digest.update((_canonical_json([LEGACY_MANIFEST_FORMAT_VERSION, upload_id]) + "\n").encode("utf-8"))
    for chunk in chunks:
        object_key_digest = hashlib.sha256(
            f"legacy-sales-upload-object-key-v1\n{chunk['objectKey']}".encode("utf-8")
        ).hexdigest()
        created_at = _normalized_datetime(
            chunk["createdAt"], "cleanup chunk createdAt"
        ).isoformat()
        digest.update((_canonical_json([
            chunk["chunkIndex"],
            object_key_digest,
            chunk["sizeBytes"],
            chunk["sha256"],
            created_at,
        ]) + "\n").encode("utf-8"))
    return digest.hexdigest()


def _exact_keys(value: object, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise SalesCutoverAttestationError(f"{label} 字段集合无效")
    return value


def _safe_integer(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SalesCutoverAttestationError(f"{label} 不是有效整数")
    return value


def _normalized_datetime(value: object, label: str):
    if not isinstance(value, str):
        raise SalesCutoverAttestationError(f"{label} 不是有效时间")
    parsed = parse_datetime(value)
    if parsed is None:
        raise SalesCutoverAttestationError(f"{label} 不是有效时间")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, UTC)
    return parsed.astimezone(UTC)


def _source_uri(path: Path) -> str:
    return f"file:{path.as_posix()}?mode=ro"


def _source_path(value: str | os.PathLike[str]) -> Path:
    raw = Path(value)
    if not raw.is_absolute() or raw.suffix.lower() != ".sqlite":
        raise SalesCutoverAttestationError("D1 source 必须是精确的绝对 .sqlite 路径")
    try:
        source = raw.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise SalesCutoverAttestationError("D1 source 不存在或无法解析") from error
    if not source.is_file():
        raise SalesCutoverAttestationError("D1 source 不是文件")
    return source


def _cutover_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not CUTOVER_ID_RE.fullmatch(normalized):
        raise SalesCutoverAttestationError("cutover_id 必须为 8 到 128 位安全标识")
    return normalized


def _run_id(value: str, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if not RUN_ID_RE.fullmatch(normalized):
        raise SalesCutoverAttestationError(f"{label} 无效")
    return normalized


def _read_d1_terminal_payload(source: Path, cutover_id: str) -> dict[str, object]:
    before = source.stat()
    connection = sqlite3.connect(_source_uri(source), uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        authority = connection.execute(
            "SELECT owner, epoch, cutover_id, updated_at "
            "FROM sales_write_authority WHERE id = 1 LIMIT 1"
        ).fetchone()
        if authority is None:
            raise SalesCutoverAttestationError("D1 缺少销售写入 authority 终态证明")
        epoch = _safe_integer(authority["epoch"], "D1 authority epoch", minimum=1)
        if authority["owner"] != "postgresql" or authority["cutover_id"] != cutover_id:
            raise SalesCutoverAttestationError("D1 尚未以相同 cutover_id 进入 PostgreSQL 终态")
        trigger_rows = connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'trigger' AND name LIKE 'sales_authority_%' "
            "ORDER BY name COLLATE BINARY"
        ).fetchall()
        schema_material = [(str(row["name"]), str(row["sql"] or "")) for row in trigger_rows]
        schema_digest = _payload_sha256(schema_material)
        if (
            len(schema_material) != EXPECTED_D1_AUTHORITY_TRIGGER_COUNT
            or schema_digest != EXPECTED_D1_AUTHORITY_SCHEMA_SHA256
        ):
            raise SalesCutoverAttestationError("D1 authority 写栅栏不完整或语义与 0090 不一致")
        blockers = {
            "processingBatches": int(connection.execute(
                "SELECT COUNT(*) FROM sales_import_batches WHERE status = 'processing'"
            ).fetchone()[0]),
            "activeUploads": int(connection.execute(
                "SELECT COUNT(*) FROM sales_import_uploads "
                "WHERE status IN ('uploading','ready','processing') "
                "AND (datetime(expires_at) IS NULL OR datetime(expires_at) > datetime('now'))"
            ).fetchone()[0]),
            "uploadChunks": int(connection.execute(
                "SELECT COUNT(*) FROM sales_import_upload_chunks"
            ).fetchone()[0]),
            "processingFingerprints": int(connection.execute(
                "SELECT COUNT(*) FROM import_content_fingerprints "
                "WHERE domain='sales' AND status='processing'"
            ).fetchone()[0]),
            "processingAttempts": int(connection.execute(
                "SELECT COUNT(*) FROM import_content_attempts "
                "WHERE domain='sales' AND outcome='processing'"
            ).fetchone()[0]),
            "processingScopeHeads": int(connection.execute(
                "SELECT COUNT(*) FROM import_scope_heads "
                "WHERE domain='sales' AND status='processing'"
            ).fetchone()[0]),
        }
        if any(value != 0 for value in blockers.values()):
            raise SalesCutoverAttestationError("D1 terminal authority 仍包含销售写入 blocker")
        observed_at = timezone.now().astimezone(UTC)
        payload: dict[str, object] = {
            "schemaVersion": ATTESTATION_VERSION,
            "cutoverId": cutover_id,
            "observedAt": observed_at.isoformat(),
            "d1Authority": {
                "owner": "postgresql",
                "epoch": epoch,
                "updatedAt": _normalized_datetime(
                    authority["updated_at"], "D1 authority updated_at"
                ).isoformat(),
            },
            "d1Blockers": blockers,
            "source": {
                "pathSha256": hashlib.sha256(str(source).encode("utf-8")).hexdigest(),
                "fileIdentitySha256": hashlib.sha256(
                    f"{before.st_dev}\n{before.st_ino}".encode("ascii")
                ).hexdigest(),
                "sizeBytes": before.st_size,
                "authoritySchemaSha256": schema_digest,
            },
        }
        connection.rollback()
    except sqlite3.Error as error:
        raise SalesCutoverAttestationError("无法只读核验 D1 terminal authority") from error
    finally:
        connection.close()
    after = source.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise SalesCutoverAttestationError("生成 attestation 期间 D1 文件发生变化")
    return payload


def _read_d1_core_evidence(connection: sqlite3.Connection) -> dict[str, object]:
    tables: dict[str, object] = {}
    for table_name, where in CLEANUP_CORE_SPECS:
        columns = connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        if not columns:
            raise SalesCutoverAttestationError(f"D1 缺少 cleanup 核心表 {table_name}")
        names = [str(column[1]) for column in columns]
        digest = hashlib.sha256()
        schema = {
            "format": "sales-d1-core-table-v1",
            "table": table_name,
            "where": where,
            "schema": [
                {
                    "cid": int(column[0]),
                    "name": str(column[1]),
                    "type": str(column[2]),
                    "notnull": int(column[3]),
                    "default": None if column[4] is None else str(column[4]),
                    "pk": int(column[5]),
                }
                for column in columns
            ],
        }
        digest.update(_canonical_json(schema).encode("utf-8"))
        count = 0
        cursor = connection.execute(
            f'SELECT rowid AS "__teruisi_rowid", * FROM "{table_name}" '
            f"{where} ORDER BY rowid ASC"
        )
        for row in cursor:
            serialized = _canonical_json([row[0], *row[1 : len(names) + 1]])
            encoded = serialized.encode("utf-8")
            digest.update(f"{len(encoded)}:".encode("ascii"))
            digest.update(encoded)
            count += 1
        tables[table_name] = {"rowCount": count, "sha256": digest.hexdigest()}
    return {"format": "sales-d1-core-evidence-v1", "tables": tables}


def _target_snapshot(batch_size: int = 1000) -> tuple[dict[str, int], dict[str, str]]:
    # Local import avoids an authority/readiness -> management command cycle.
    from .management.commands import migrate_sales_from_d1 as migration

    counts: dict[str, int] = {}
    digests: dict[str, str] = {}
    for spec in migration.SPECS:
        counts[spec.source_table], digests[spec.source_table] = migration._target_digest(
            spec, batch_size
        )
    count, digest = migration._target_projection_digest(batch_size)
    counts[migration.QUERY_PROJECTION_DIGEST_KEY] = count
    digests[migration.QUERY_PROJECTION_DIGEST_KEY] = digest
    extra_counts, extra_digests = migration._target_control_snapshot()
    counts.update(extra_counts)
    digests.update(extra_digests)
    extra_counts, extra_digests = migration._target_legacy_upload_snapshot()
    counts.update(extra_counts)
    digests.update(extra_digests)
    return counts, digests


def _validate_snapshot_maps(counts: object, digests: object, label: str) -> None:
    count_map = _exact_keys(counts, TARGET_SNAPSHOT_KEYS, f"{label} counts")
    digest_map = _exact_keys(digests, TARGET_SNAPSHOT_KEYS, f"{label} digests")
    for key in TARGET_SNAPSHOT_KEYS:
        _safe_integer(count_map[key], f"{label} {key} count")
        if not isinstance(digest_map[key], str) or not HEX_64_RE.fullmatch(digest_map[key]):
            raise SalesCutoverAttestationError(f"{label} {key} digest 无效")


def _migration_evidence(
    *,
    source_path_digest: str,
    apply_run_id: str,
    verify_run_id: str,
    verify_live_target: bool = True,
) -> dict[str, object]:
    verify_id = _run_id(verify_run_id, "migration verify run id")
    apply_id = _run_id(apply_run_id, "migration apply run id")
    try:
        verify = SalesMigrationRun.objects.get(id=verify_id)
    except SalesMigrationRun.DoesNotExist as error:
        raise SalesCutoverAttestationError("缺少指定的 v4 migration verify run") from error
    from .management.commands import migrate_sales_from_d1 as migration

    canonical_format_version = ATTESTED_MIGRATION_FORMAT_VERSION

    if (
        verify.status != "verified"
        or verify.dry_run
        or verify.completed_at is None
        or verify.canonical_format_version != canonical_format_version
        or verify.source_path_digest != source_path_digest
        or not verify.source_revision
        or verify.source_revision != verify.target_revision
        or verify.source_counts != verify.target_counts
        or verify.source_digests != verify.target_digests
    ):
        raise SalesCutoverAttestationError("指定的 v4 migration verify run 不是完整成功证明")
    _validate_snapshot_maps(verify.target_counts, verify.target_digests, "verify target")
    try:
        apply = SalesMigrationRun.objects.get(id=apply_id)
    except SalesMigrationRun.DoesNotExist as error:
        raise SalesCutoverAttestationError("缺少指定的 v4 migration apply run") from error
    if (
        apply.status != "completed"
        or apply.dry_run
        or apply.completed_at is None
        or not apply.approved_run_id
        or apply.source_path_digest != source_path_digest
        or apply.canonical_format_version != canonical_format_version
        or apply.source_revision != verify.source_revision
        or apply.target_revision != verify.target_revision
        or apply.source_counts != verify.source_counts
        or apply.target_counts != verify.target_counts
        or apply.source_digests != verify.source_digests
        or apply.target_digests != verify.target_digests
    ):
        raise SalesCutoverAttestationError("指定的 v4 migration apply run 与 verify 不一致")
    try:
        approval = SalesMigrationRun.objects.get(id=apply.approved_run_id)
    except SalesMigrationRun.DoesNotExist as error:
        raise SalesCutoverAttestationError("v4 apply 缺少已消费 dry-run 审批") from error
    if (
        not approval.dry_run
        or approval.status != "dry_run_completed"
        or approval.completed_at is None
        or approval.consumed_by_run_id != apply.id
        or approval.approval_consumed_at is None
        or approval.source_path_digest != source_path_digest
        or approval.canonical_format_version != canonical_format_version
        or approval.source_revision != verify.source_revision
        or approval.source_counts != verify.source_counts
        or approval.source_digests != verify.source_digests
    ):
        raise SalesCutoverAttestationError("v4 apply 的 dry-run 审批消费证明无效")
    if verify_live_target:
        live_counts, live_digests = _target_snapshot()
        if live_counts != verify.target_counts or live_digests != verify.target_digests:
            raise SalesCutoverAttestationError("PostgreSQL 当前销售目标已偏离 v4 verify 证明")
        revisions = {
            domain: (int(revision), str(source_digest or ""))
            for domain, revision, source_digest in SalesDataRevision.objects.filter(
                domain__in=["sales", "erp"]
            ).values_list("domain", "revision", "source_digest")
        }
        try:
            sales_revision, erp_revision = (
                int(part) for part in verify.target_revision.split(":")
            )
        except (TypeError, ValueError) as error:
            raise SalesCutoverAttestationError("v4 migration revision 证明无效") from error
        expected_revisions = {
            "sales": (
                sales_revision,
                migration._domain_digest("sales", verify.target_digests),
            ),
        }
        from erp_reference.models import ErpReferenceSyncCheckpoint

        checkpoint = ErpReferenceSyncCheckpoint.objects.filter(id=1).first()
        if checkpoint is None:
            expected_revisions["erp"] = (
                erp_revision,
                migration._domain_digest("erp", verify.target_digests),
            )
        else:
            age_seconds = (timezone.now() - checkpoint.last_checked_at).total_seconds()
            if (
                checkpoint.source_path_digest != source_path_digest
                or checkpoint.erp_revision != erp_revision
                or checkpoint.row_count != verify.target_counts["erp_product_master"]
                or checkpoint.row_count <= 0
                or not HEX_64_RE.fullmatch(checkpoint.content_hash)
                or not re.fullmatch(r"[0-9a-f]{32}", checkpoint.source_epoch)
                or age_seconds < -5
                or age_seconds
                > int(getattr(settings, "ERP_REFERENCE_SYNC_MAX_AGE_SECONDS", 300))
            ):
                raise SalesCutoverAttestationError(
                    "PostgreSQL ERP checkpoint 与 v4 verify/新鲜度不一致"
                )
            expected_revisions["erp"] = (
                checkpoint.erp_revision,
                checkpoint.content_hash,
            )
        if revisions != expected_revisions:
            raise SalesCutoverAttestationError(
                "PostgreSQL 当前 revision/digest 已偏离 v4 verify 证明"
            )
    return {
        "applyRunId": apply.id,
        "verifyRunId": verify.id,
        "canonicalFormatVersion": canonical_format_version,
        "sourceRevision": verify.source_revision,
        "targetCounts": verify.target_counts,
        "targetDigests": verify.target_digests,
    }


def _cleanup_manifest(
    *,
    source: Path,
    cutover_id: str,
    apply_run_id: str,
    verify_run_id: str,
    manifest_path: str | os.PathLike[str],
    migration_evidence: dict[str, object],
) -> dict[str, object]:
    raw_path = Path(manifest_path)
    if not raw_path.is_absolute() or raw_path.suffix.lower() != ".json":
        raise SalesCutoverAttestationError("cleanup manifest 必须是精确绝对 .json 路径")
    try:
        resolved = raw_path.resolve(strict=True)
        raw = resolved.read_bytes()
        manifest = json.loads(raw)
    except (OSError, RuntimeError, json.JSONDecodeError) as error:
        raise SalesCutoverAttestationError("cleanup manifest 不存在或不是有效 JSON") from error
    expected_keys = {
        "version", "manifestId", "cutoverId", "sourcePathDigest", "bucket",
        "persistPathDigest", "plannedAt", "status", "sessions", "objects",
        "coreEvidence", "verifiedMissingObjectKeys", "lockedVerifyRunId",
        "lockedApplyRunId", "lockedVerifyRecordedAt", "metadataDeletedAt", "completedAt",
    }
    manifest = _exact_keys(manifest, expected_keys, "cleanup manifest")
    source_digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()
    if (
        manifest["version"] != CLEANUP_MANIFEST_VERSION
        or manifest["cutoverId"] != cutover_id
        or manifest["sourcePathDigest"] != source_digest
        or manifest["bucket"] != "site-creator-r2"
        or not isinstance(manifest["persistPathDigest"], str)
        or not HEX_64_RE.fullmatch(manifest["persistPathDigest"])
        or manifest["status"] != "completed"
        or manifest["lockedApplyRunId"] != apply_run_id
        or manifest["lockedVerifyRunId"] != verify_run_id
    ):
        raise SalesCutoverAttestationError("cleanup manifest 身份或完成状态无效")
    for field in ("plannedAt", "lockedVerifyRecordedAt", "metadataDeletedAt", "completedAt"):
        _normalized_datetime(manifest[field], f"cleanup manifest {field}")
    sessions = manifest["sessions"]
    objects = manifest["objects"]
    if not isinstance(sessions, list) or not isinstance(objects, list):
        raise SalesCutoverAttestationError("cleanup manifest staging 清单无效")
    session_ids: list[str] = []
    for item in sessions:
        item = _exact_keys(item, {
            "id", "status", "fileSizeBytes", "chunkSizeBytes", "chunkCount",
            "receivedChunkCount", "receivedBytes", "createdAt", "updatedAt", "expiresAt",
        }, "cleanup session")
        if (
            not isinstance(item["id"], str)
            or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", item["id"])
            or item["status"] not in {"uploading", "ready", "processing", "completed"}
        ):
            raise SalesCutoverAttestationError("cleanup session 身份或状态无效")
        _safe_integer(item["fileSizeBytes"], "cleanup fileSizeBytes", minimum=1)
        _safe_integer(item["chunkSizeBytes"], "cleanup chunkSizeBytes", minimum=1)
        _safe_integer(item["chunkCount"], "cleanup chunkCount", minimum=1)
        _safe_integer(item["receivedChunkCount"], "cleanup receivedChunkCount")
        _safe_integer(item["receivedBytes"], "cleanup receivedBytes")
        _normalized_datetime(item["createdAt"], "cleanup session createdAt")
        _normalized_datetime(item["updatedAt"], "cleanup session updatedAt")
        _normalized_datetime(item["expiresAt"], "cleanup session expiresAt")
        session_ids.append(item["id"])
    if session_ids != sorted(set(session_ids)):
        raise SalesCutoverAttestationError("cleanup session 顺序或唯一性无效")
    object_keys: list[str] = []
    grouped: dict[str, list[dict[str, Any]]] = {item: [] for item in session_ids}
    for item in objects:
        item = _exact_keys(
            item, {"uploadId", "chunkIndex", "objectKey", "sizeBytes", "sha256", "createdAt"},
            "cleanup object",
        )
        upload_id = item["uploadId"]
        object_key = item["objectKey"]
        if (
            upload_id not in grouped
            or not isinstance(object_key, str)
            or not object_key.startswith(f"sales-upload/{upload_id}/")
            or "\\" in object_key
            or len(object_key) > 1024
        ):
            raise SalesCutoverAttestationError("cleanup object_key 越界")
        _safe_integer(item["chunkIndex"], "cleanup chunkIndex")
        _safe_integer(item["sizeBytes"], "cleanup sizeBytes", minimum=1)
        if not isinstance(item["sha256"], str) or not HEX_64_RE.fullmatch(item["sha256"]):
            raise SalesCutoverAttestationError("cleanup object sha256 无效")
        _normalized_datetime(item["createdAt"], "cleanup object createdAt")
        object_keys.append(object_key)
        grouped[upload_id].append(item)
    ordered = sorted(objects, key=lambda item: (item["uploadId"], item["chunkIndex"]))
    if objects != ordered or len(object_keys) != len(set(object_keys)):
        raise SalesCutoverAttestationError("cleanup object 顺序或唯一性无效")
    missing = manifest["verifiedMissingObjectKeys"]
    if not isinstance(missing, list) or missing != sorted(object_keys):
        raise SalesCutoverAttestationError("cleanup object 缺失回查证明不完整")
    core = _exact_keys(manifest["coreEvidence"], {"format", "tables"}, "cleanup core evidence")
    tables = _exact_keys(core["tables"], CLEANUP_CORE_TABLES, "cleanup core tables")
    if core["format"] != "sales-d1-core-evidence-v1":
        raise SalesCutoverAttestationError("cleanup core evidence version 无效")
    for name, evidence in tables.items():
        evidence = _exact_keys(evidence, {"rowCount", "sha256"}, f"cleanup core {name}")
        _safe_integer(evidence["rowCount"], f"cleanup core {name} rowCount")
        if not isinstance(evidence["sha256"], str) or not HEX_64_RE.fullmatch(evidence["sha256"]):
            raise SalesCutoverAttestationError("cleanup core evidence digest 无效")
    identity = {
        key: manifest[key]
        for key in (
            "version", "cutoverId", "sourcePathDigest", "bucket", "persistPathDigest",
            "plannedAt", "sessions", "objects", "coreEvidence",
        )
    }
    expected_manifest_id = _payload_sha256(identity)
    if manifest["manifestId"] != expected_manifest_id:
        raise SalesCutoverAttestationError("cleanup manifest 摘要不匹配")
    target_counts = migration_evidence["targetCounts"]
    if target_counts["sales_import_upload_chunks"] != len(objects):
        raise SalesCutoverAttestationError("cleanup manifest 未覆盖 v4 归档的全部 R2 分片")
    if target_counts["sales_import_uploads"] != len(sessions):
        raise SalesCutoverAttestationError("cleanup manifest 未覆盖 v4 归档的全部上传会话")
    audits = SalesLegacyUploadAudit.objects.in_bulk(session_ids)
    if set(audits) != set(session_ids):
        raise SalesCutoverAttestationError("PostgreSQL 缺少 cleanup 会话的 legacy audit")
    for session in sessions:
        audit = audits[session["id"]]
        chunks = grouped[session["id"]]
        indexes = [item["chunkIndex"] for item in chunks]
        if indexes != list(range(len(chunks))):
            raise SalesCutoverAttestationError("cleanup 分片索引不连续")
        expected_chunk_count = (
            session["fileSizeBytes"] + session["chunkSizeBytes"] - 1
        ) // session["chunkSizeBytes"]
        if session["chunkCount"] != expected_chunk_count:
            raise SalesCutoverAttestationError("cleanup 会话声明分片数与文件大小不一致")
        if (
            session["receivedChunkCount"] > session["chunkCount"]
            or session["receivedBytes"] > session["fileSizeBytes"]
        ):
            raise SalesCutoverAttestationError("cleanup 会话接收计数越界")
        if session["status"] == "completed":
            if (
                session["receivedChunkCount"] != session["chunkCount"]
                or session["receivedBytes"] != session["fileSizeBytes"]
                or len(chunks) not in {0, session["chunkCount"]}
            ):
                raise SalesCutoverAttestationError("cleanup completed 会话声明不完整")
        elif (
            len(chunks) != session["receivedChunkCount"]
            or sum(item["sizeBytes"] for item in chunks) != session["receivedBytes"]
        ):
            raise SalesCutoverAttestationError("cleanup 过期会话分片与接收计数不一致")
        for chunk in chunks:
            expected_size = (
                session["fileSizeBytes"]
                - session["chunkSizeBytes"] * (session["chunkCount"] - 1)
                if chunk["chunkIndex"] == session["chunkCount"] - 1
                else session["chunkSizeBytes"]
            )
            if chunk["sizeBytes"] != expected_size:
                raise SalesCutoverAttestationError("cleanup 分片大小与会话声明不一致")
        if (
            audit.source_status != session["status"]
            or audit.archive_reason
            != ("completed" if session["status"] == "completed" else "expired")
            or audit.file_size_bytes != session["fileSizeBytes"]
            or audit.chunk_size_bytes != session["chunkSizeBytes"]
            or audit.declared_chunk_count != session["chunkCount"]
            or audit.declared_received_chunk_count != session["receivedChunkCount"]
            or audit.declared_received_bytes != session["receivedBytes"]
            or audit.source_created_at.astimezone(UTC)
            != _normalized_datetime(session["createdAt"], "cleanup createdAt")
            or audit.source_updated_at.astimezone(UTC)
            != _normalized_datetime(session["updatedAt"], "cleanup updatedAt")
            or audit.source_expires_at.astimezone(UTC)
            != _normalized_datetime(session["expiresAt"], "cleanup expiresAt")
            or audit.manifest_chunk_count != len(chunks)
            or audit.manifest_bytes != sum(item["sizeBytes"] for item in chunks)
            or audit.manifest_sha256 != _legacy_manifest_digest(session["id"], chunks)
        ):
            raise SalesCutoverAttestationError("cleanup manifest 与 PostgreSQL legacy audit 不一致")
    connection = sqlite3.connect(_source_uri(source), uri=True, timeout=30)
    try:
        connection.execute("PRAGMA query_only = ON")
        if _read_d1_core_evidence(connection) != core:
            raise SalesCutoverAttestationError("D1 当前核心事实/控制摘要与 cleanup 证明不一致")
        if int(connection.execute("SELECT COUNT(*) FROM sales_import_upload_chunks").fetchone()[0]) != 0:
            raise SalesCutoverAttestationError("D1 cleanup 后仍有上传分片")
        if int(connection.execute("SELECT COUNT(*) FROM sales_import_uploads").fetchone()[0]) != 0:
            raise SalesCutoverAttestationError("D1 cleanup 后仍有上传会话")
        for session_id in session_ids:
            if connection.execute(
                "SELECT 1 FROM sales_import_uploads WHERE id=? LIMIT 1", (session_id,)
            ).fetchone() is not None:
                raise SalesCutoverAttestationError("D1 cleanup 会话元数据仍存在")
    except sqlite3.Error as error:
        raise SalesCutoverAttestationError("无法回查 D1 cleanup 元数据") from error
    finally:
        connection.close()
    return {
        "manifestId": manifest["manifestId"],
        "manifestSha256": hashlib.sha256(raw).hexdigest(),
        "sessionCount": len(sessions),
        "objectCount": len(objects),
        "coreEvidenceSha256": _payload_sha256(core),
        "lockedVerifyRunId": manifest["lockedVerifyRunId"],
        "completedAt": _normalized_datetime(
            manifest["completedAt"], "cleanup completedAt"
        ).isoformat(),
    }


def validate_postgresql_cutover_evidence(
    *,
    source: str | os.PathLike[str],
    cutover_id: str,
    migration_apply_run_id: str,
    migration_verify_run_id: str,
    cleanup_manifest: str | os.PathLike[str],
) -> dict[str, object]:
    source_path = _source_path(source)
    cutover = _cutover_id(cutover_id)
    verify_id = _run_id(migration_verify_run_id, "migration verify run id")
    apply_id = _run_id(migration_apply_run_id, "migration apply run id")
    source_digest = hashlib.sha256(str(source_path).encode("utf-8")).hexdigest()
    migration = _migration_evidence(
        source_path_digest=source_digest,
        apply_run_id=apply_id,
        verify_run_id=verify_id,
    )
    cleanup = _cleanup_manifest(
        source=source_path,
        cutover_id=cutover,
        apply_run_id=apply_id,
        verify_run_id=verify_id,
        manifest_path=cleanup_manifest,
        migration_evidence=migration,
    )
    return {"postgresqlMigration": migration, "legacyCleanup": cleanup}


def validate_postgresql_migration_evidence(
    *,
    source: str | os.PathLike[str],
    migration_apply_run_id: str,
    migration_verify_run_id: str,
) -> dict[str, object]:
    source_path = _source_path(source)
    return _migration_evidence(
        source_path_digest=hashlib.sha256(str(source_path).encode("utf-8")).hexdigest(),
        apply_run_id=_run_id(migration_apply_run_id, "migration apply run id"),
        verify_run_id=_run_id(migration_verify_run_id, "migration verify run id"),
        verify_live_target=True,
    )


def _validate_attestation_row(
    attestation: SalesCutoverAttestation,
    *,
    cutover_id: str,
    payload_sha256: str | None = None,
    verify_live_baseline: bool = False,
) -> dict[str, object]:
    payload = _exact_keys(attestation.payload, {
        "schemaVersion", "cutoverId", "observedAt", "d1Authority", "d1Blockers",
        "source", "postgresqlMigration", "legacyCleanup",
    }, "cutover attestation payload")
    digest = _payload_sha256(payload)
    if (
        attestation.cutover_id != cutover_id
        or attestation.payload_sha256 != digest
        or (payload_sha256 is not None and digest != payload_sha256)
        or payload["schemaVersion"] != ATTESTATION_VERSION
        or payload["cutoverId"] != cutover_id
    ):
        raise SalesCutoverAttestationError("cutover attestation 摘要或身份不匹配")
    authority = _exact_keys(payload["d1Authority"], {"owner", "epoch", "updatedAt"}, "D1 authority")
    blockers = _exact_keys(payload["d1Blockers"], BLOCKER_KEYS, "D1 blockers")
    source = _exact_keys(
        payload["source"],
        {"pathSha256", "fileIdentitySha256", "sizeBytes", "authoritySchemaSha256"},
        "D1 source",
    )
    if (
        authority["owner"] != "postgresql"
        or _safe_integer(authority["epoch"], "D1 authority epoch", minimum=1)
        != attestation.d1_authority_epoch
        or any(_safe_integer(blockers[key], f"D1 blocker {key}") != 0 for key in BLOCKER_KEYS)
        or source["pathSha256"] != attestation.source_path_digest
        or not isinstance(source["fileIdentitySha256"], str)
        or not HEX_64_RE.fullmatch(source["fileIdentitySha256"])
        or _safe_integer(source["sizeBytes"], "D1 sizeBytes", minimum=1) < 1
        or source["authoritySchemaSha256"] != EXPECTED_D1_AUTHORITY_SCHEMA_SHA256
    ):
        raise SalesCutoverAttestationError("cutover attestation D1 terminal 证明无效")
    _normalized_datetime(authority["updatedAt"], "D1 authority updatedAt")
    observed_at = _normalized_datetime(payload["observedAt"], "attestation observedAt")
    if observed_at != attestation.observed_at.astimezone(UTC):
        raise SalesCutoverAttestationError("cutover attestation observedAt 不匹配")
    migration_payload = _exact_keys(payload["postgresqlMigration"], {
        "applyRunId", "verifyRunId", "canonicalFormatVersion", "sourceRevision",
        "targetCounts", "targetDigests",
    }, "attestation PostgreSQL migration")
    if (
        migration_payload["applyRunId"] != attestation.migration_apply_run_id
        or migration_payload["verifyRunId"] != attestation.migration_verify_run_id
        or migration_payload["canonicalFormatVersion"] != ATTESTED_MIGRATION_FORMAT_VERSION
        or not isinstance(migration_payload["sourceRevision"], str)
        or not re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", migration_payload["sourceRevision"])
    ):
        raise SalesCutoverAttestationError("attestation PostgreSQL migration 字段无效")
    _run_id(migration_payload["applyRunId"], "attestation applyRunId")
    _run_id(migration_payload["verifyRunId"], "attestation verifyRunId")
    _validate_snapshot_maps(
        migration_payload["targetCounts"],
        migration_payload["targetDigests"],
        "attestation migration target",
    )
    migration = migration_payload
    if verify_live_baseline:
        live_migration = _migration_evidence(
            source_path_digest=attestation.source_path_digest,
            apply_run_id=attestation.migration_apply_run_id,
            verify_run_id=attestation.migration_verify_run_id,
            verify_live_target=True,
        )
        if migration_payload != live_migration:
            raise SalesCutoverAttestationError("attestation PostgreSQL migration 证明无效")
    cleanup = _exact_keys(payload["legacyCleanup"], {
        "manifestId", "manifestSha256", "sessionCount", "objectCount",
        "coreEvidenceSha256", "lockedVerifyRunId", "completedAt",
    }, "attestation cleanup")
    if (
        cleanup["manifestId"] != attestation.cleanup_manifest_id
        or cleanup["manifestSha256"] != attestation.cleanup_manifest_sha256
        or cleanup["lockedVerifyRunId"] != attestation.migration_verify_run_id
        or not all(
            isinstance(cleanup[key], str) and HEX_64_RE.fullmatch(cleanup[key])
            for key in ("manifestId", "manifestSha256", "coreEvidenceSha256")
        )
    ):
        raise SalesCutoverAttestationError("attestation cleanup 摘要证明无效")
    _safe_integer(cleanup["sessionCount"], "cleanup sessionCount")
    _safe_integer(cleanup["objectCount"], "cleanup objectCount")
    _normalized_datetime(cleanup["completedAt"], "cleanup completedAt")
    if (
        attestation.migration_apply_run_id != migration_payload["applyRunId"]
        or attestation.migration_verify_run_id != migration_payload["verifyRunId"]
    ):
        raise SalesCutoverAttestationError("attestation migration run 字段不匹配")
    return payload


def _stable_evidence(payload: dict[str, object]) -> dict[str, object]:
    return {key: payload[key] for key in payload if key != "observedAt"}


def require_valid_cutover_attestation(
    *,
    cutover_id: str,
    payload_sha256: str | None = None,
    verify_live_baseline: bool = False,
) -> dict[str, object]:
    cutover = _cutover_id(cutover_id)
    if payload_sha256 is not None and not HEX_64_RE.fullmatch(payload_sha256):
        raise SalesCutoverAttestationError("attestation sha256 无效")
    try:
        attestation = SalesCutoverAttestation.objects.get(cutover_id=cutover)
    except SalesCutoverAttestation.DoesNotExist as error:
        raise SalesCutoverAttestationError("缺少 D1 terminal cutover attestation") from error
    return _validate_attestation_row(
        attestation,
        cutover_id=cutover,
        payload_sha256=payload_sha256,
        verify_live_baseline=verify_live_baseline,
    )


def record_d1_terminal_attestation(
    *,
    source: str | os.PathLike[str],
    cutover_id: str,
    migration_apply_run_id: str,
    migration_verify_run_id: str,
    cleanup_manifest: str | os.PathLike[str],
) -> SalesCutoverAttestation:
    source_path = _source_path(source)
    cutover = _cutover_id(cutover_id)
    payload = _read_d1_terminal_payload(source_path, cutover)
    evidence = validate_postgresql_cutover_evidence(
        source=source_path,
        cutover_id=cutover,
        migration_apply_run_id=migration_apply_run_id,
        migration_verify_run_id=migration_verify_run_id,
        cleanup_manifest=cleanup_manifest,
    )
    payload.update(evidence)
    digest = _payload_sha256(payload)
    migration = evidence["postgresqlMigration"]
    cleanup = evidence["legacyCleanup"]
    with transaction.atomic():
        acquire_sales_write_authority_exclusive_lock()
        try:
            authority = SalesWriteAuthority.objects.select_for_update().get(id=1)
        except SalesWriteAuthority.DoesNotExist as error:
            raise SalesCutoverAttestationError("PostgreSQL 销售写入 authority 尚未初始化") from error
        if authority.status not in {"pending", "active"} or authority.cutover_id != cutover:
            raise SalesCutoverAttestationError("PostgreSQL authority 未以相同 cutover_id 完成 prepare")
        existing = SalesCutoverAttestation.objects.select_for_update().filter(cutover_id=cutover).first()
        if existing is not None:
            existing_payload = _validate_attestation_row(existing, cutover_id=cutover)
            if _stable_evidence(existing_payload) != _stable_evidence(payload):
                raise SalesCutoverAttestationError("已有 attestation 与当前完整 cutover 证据不一致")
            return existing
        if authority.status != "pending":
            raise SalesCutoverAttestationError("active authority 不允许补造 attestation")
        return SalesCutoverAttestation.objects.create(
            cutover_id=cutover,
            d1_authority_epoch=payload["d1Authority"]["epoch"],
            source_path_digest=payload["source"]["pathSha256"],
            migration_apply_run_id=migration["applyRunId"],
            migration_verify_run_id=migration["verifyRunId"],
            cleanup_manifest_id=cleanup["manifestId"],
            cleanup_manifest_sha256=cleanup["manifestSha256"],
            payload=payload,
            payload_sha256=digest,
            observed_at=_normalized_datetime(payload["observedAt"], "observedAt"),
        )


def attestation_envelope(attestation: SalesCutoverAttestation) -> dict[str, Any]:
    payload = _validate_attestation_row(attestation, cutover_id=attestation.cutover_id)
    return {
        "schemaVersion": ATTESTATION_VERSION,
        "payload": payload,
        "payloadSha256": attestation.payload_sha256,
    }


def save_attestation_file(
    attestation: SalesCutoverAttestation,
    *,
    audit_directory: str | os.PathLike[str],
) -> Path:
    directory = Path(audit_directory)
    if not directory.is_absolute():
        raise SalesCutoverAttestationError("audit directory 必须是绝对路径")
    directory.mkdir(parents=True, exist_ok=True)
    directory = directory.resolve(strict=True)
    if not directory.is_dir():
        raise SalesCutoverAttestationError("audit directory 不是目录")
    envelope = attestation_envelope(attestation)
    content = json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    name = hashlib.sha256(attestation.cutover_id.encode("utf-8")).hexdigest()[:24]
    destination = directory / f"sales-cutover-{name}.attestation.json"
    if destination.exists():
        if destination.read_text(encoding="utf-8") != content:
            raise SalesCutoverAttestationError("现有 attestation 文件内容冲突")
        return destination
    temporary = directory / f".{destination.name}.{os.getpid()}.tmp"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination

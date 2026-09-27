"""Operator-only source freeze, deployed checks and terminal retirement."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ai_assistant.control_models import AiWriteAuthority, AiWriteReceipt
from ai_assistant.models import HISTORICAL_MODELS
from ai_assistant.policy import digest as sha256_json
from .ai_write_authority import (
    verified_apply as _verified_apply,
    checked_path,
    CUTOVER_ID_RE,
    RUN_ID_RE,
)
from ai_assistant.migration_service import source_snapshot


def _source_snapshot(path):
    return source_snapshot(path)["rows"]


CHECKS = {
    "reader",
    "writer",
    "publicAiPages",
    "publicInvalidWritesRejected",
    "crossOriginRejected",
    "unknownAccountRejected",
    "unsignedRejected",
    "legacySourceRejected",
    "legacyPathsAbsent",
    "otherDomainsReady",
    "migratedModelCount",
    "effectiveWorkerHead",
}


def _effective_worker_head():
    node = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "nodejs/node.exe"
    tool = Path("D:/运营管理系统/tools/worker-local-release-rotation.mjs")
    if any(
        not p.is_file()
        or any(v.is_symlink() or v.is_junction() for v in [p, *p.parents])
        for p in [node, tool]
    ):
        raise CommandError("Worker effective-head verifier path is invalid")
    result = subprocess.run(
        [str(node), str(tool), "resolve", "--json"],
        capture_output=True,
        timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode or len(result.stdout) > 65536:
        raise CommandError("Worker effective-head verification failed")
    value = json.loads(result.stdout)
    expected = {
        "status",
        "version",
        "releaseId",
        "manifestPath",
        "manifestSha256",
        "guardReceiptSha256",
        "successorCount",
        "chainStateSha256",
    }
    if (
        set(value) != expected
        or value["status"] != "resolved"
        or value["version"] != "teruisi-local-worker-effective-release-v1"
    ):
        raise CommandError("Worker effective-head result is invalid")
    manifest = Path(value["manifestPath"])
    if (
        not manifest.is_relative_to(
            Path("D:/teruisi-runtime/teruisi-worker-sales/releases")
        )
        or _sha(manifest.read_bytes()) != value["manifestSha256"]
    ):
        raise CommandError("Worker effective-head manifest mismatch")
    return value


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(
            value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        stream.flush()
        os.fsync(stream.fileno())


def _preserved(source):
    excluded = set(HISTORICAL_MODELS) | {"ai_write_authority"}
    tables = [
        row[0]
        for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        if row[0] not in excluded
    ]
    result = {}
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        where, arguments = "", ()
        if table == "domain_retirement_receipts":
            where, arguments = " WHERE domain<>?", ("ai-assistant",)
        if table in {"sqlite_stat1", "sqlite_stat2", "sqlite_stat3", "sqlite_stat4"}:
            where, arguments = (
                " WHERE tbl NOT IN (" + ",".join("?" for _ in excluded) + ")",
                tuple(sorted(excluded)),
            )
        definition = (
            source.execute(
                "SELECT sql FROM sqlite_master WHERE name=?", (table,)
            ).fetchone()[0]
            or ""
        )
        primary = [
            row[1]
            for row in sorted(
                source.execute(f"PRAGMA table_info({quoted})"), key=lambda row: row[5]
            )
            if row[5]
        ]
        order = (
            ",".join('"' + name.replace('"', '""') + '"' for name in primary)
            if "WITHOUT ROWID" in definition.upper()
            else "rowid"
        )
        hasher, count = hashlib.sha256(), 0
        for row in source.execute(
            f"SELECT * FROM {quoted}{where} ORDER BY {order}", arguments
        ):
            values = [
                base64.b64encode(value).decode() if isinstance(value, bytes) else value
                for value in row
            ]
            hasher.update(sha256_json(values).encode("ascii"))
            count += 1
        if table != "domain_retirement_receipts" or count:
            result[table] = {"count": count, "sha256": hasher.hexdigest()}
    return sha256_json(result)


def _sql(name: str) -> tuple[bytes, list[str]]:
    raw = (Path(settings.BASE_DIR).parent / "drizzle" / name).read_bytes()
    return raw, [
        part.strip()
        for part in raw.decode().split("--> statement-breakpoint")
        if part.strip()
    ]


def _source_rejects(source):
    source.execute("SAVEPOINT rejection")
    try:
        for table in HISTORICAL_MODELS:
            try:
                source.execute(f'INSERT INTO "{table}" DEFAULT VALUES')
            except sqlite3.DatabaseError as error:
                if not any(
                    message in str(error)
                    for message in ("ai_authority_not_legacy", "ai_domain_retired")
                ):
                    raise CommandError(
                        "AI D1 rejection did not come from the domain fence"
                    ) from error
            else:
                raise CommandError("legacy AI D1 write was accepted")
    finally:
        source.execute("ROLLBACK TO rejection")
        source.execute("RELEASE rejection")


def _request(
    port: int,
    path: str,
    expected: int = 200,
    *,
    method="GET",
    payload=None,
    signed_email=None,
    origin=None,
):
    body = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        if payload is not None
        else b""
    )
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    if signed_email:
        principal = {
            "email": signed_email,
            "displayName": "受控系统检查",
            "role": "viewer",
            "scope": None,
        }
        encoded = (
            base64.urlsafe_b64encode(
                json.dumps(principal, separators=(",", ":")).encode()
            )
            .decode()
            .rstrip("=")
        )
        timestamp, request_id, body_hash = (
            str(int(time.time())),
            uuid.uuid4().hex,
            _sha(body),
        )
        canonical = "\n".join(
            ["v1", timestamp, request_id, method, path, "", body_hash, encoded]
        )
        signature = hmac.new(
            os.environ["TERUISI_DJANGO_INTERNAL_SECRET"].encode(),
            canonical.encode(),
            hashlib.sha256,
        ).hexdigest()
        headers.update(
            {
                "X-Teruisi-Principal": encoded,
                "X-Teruisi-Timestamp": timestamp,
                "X-Teruisi-Request-Id": request_id,
                "X-Teruisi-Content-SHA256": body_hash,
                "X-Teruisi-Signature": "v1=" + signature,
            }
        )
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body if method != "GET" else None,
        headers=headers,
        method=method,
    )
    # No proxies, redirects, external callbacks, or arbitrary destinations.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(request, timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read(2 * 1024 * 1024 + 1)
        if (
            response.status != expected
            or len(raw) > 2 * 1024 * 1024
            or response.url != request.full_url
        ):
            raise CommandError(
                f"deployed check failed: {port}{path}, expected {expected}, got {response.status}"
            )
        return json.loads(raw)


class Command(BaseCommand):
    help = "Protected ai-assistant cutover checks (no general D1/R2 shutdown)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--action",
            choices=[
                "install-authority",
                "smoke",
                "retirement-plan",
                "retirement-apply",
            ],
            required=True,
        )
        parser.add_argument("--source", required=True)
        parser.add_argument("--approved-run-id", default="")
        parser.add_argument("--cutover-id", default="")
        parser.add_argument("--release-root", default="")
        parser.add_argument("--smoke-receipt", default="")
        parser.add_argument("--approved-plan-id", default="")
        parser.add_argument("--output", required=True)

    def handle(self, *args, **options):
        if (
            settings.DJANGO_ENVIRONMENT == "production"
            and settings.DJANGO_PROCESS_ROLE != "migration_writer"
        ):
            raise CommandError("migration_writer required")
        path, output = checked_path(options["source"]), Path(options["output"])
        if (
            not path.is_absolute()
            or not path.is_file()
            or path.is_symlink()
            or path.suffix != ".sqlite"
            or not output.is_absolute()
            or output.exists()
        ):
            raise CommandError("exact regular source and create-only output required")
        action = options["action"]
        connection = sqlite3.connect(path, timeout=30, isolation_level=None)
        try:
            connection.execute("BEGIN IMMEDIATE")
            before = _preserved(connection)
            if action == "install-authority":
                source_digest = sha256_json(_source_snapshot(path))
                raw, statements = _sql("0113_ai_write_authority.sql")
                for statement in statements:
                    connection.execute(statement)
                if (
                    source_digest != sha256_json(_source_snapshot(path))
                    or _preserved(connection) != before
                ):
                    raise CommandError("authority installation changed existing facts")
                connection.commit()
                result = {
                    "status": "installed",
                    "sourceDigest": source_digest,
                    "migrationSha256": _sha(raw),
                    "preservedSha256": before,
                }
            else:
                run_id, cutover = options["approved_run_id"], options["cutover_id"]
                if not RUN_ID_RE.fullmatch(run_id) or not CUTOVER_ID_RE.fullmatch(
                    cutover
                ):
                    raise CommandError("exact migration run/cutover required")
                run = _verified_apply(path, run_id)
                authority = AiWriteAuthority.objects.get(id=1)
                legacy = connection.execute(
                    "SELECT owner,cutover_id FROM ai_write_authority WHERE id=1"
                ).fetchone()
                if (
                    authority.status != "postgres"
                    or authority.cutover_id != cutover
                    or authority.migration_verify_run_id != run_id
                    or legacy != ("postgresql", cutover)
                    or AiWriteReceipt.objects.filter(status="processing").exists()
                ):
                    raise CommandError("authority mismatch or outstanding requests")
                _source_rejects(connection)
                if action == "smoke":
                    connection.rollback()
                    effective = _effective_worker_head()
                    if (
                        Path(options["release_root"]).resolve()
                        != Path(effective["manifestPath"]).resolve().parent
                    ):
                        raise CommandError(
                            "smoke release is not the verified effective Worker head"
                        )
                    _request(8111, "/health/ready")
                    _request(8112, "/health/ready")
                    models = _request(3000, "/api/ai/models")
                    if len(models.get("items", [])) != run.source_counts["ai_models"]:
                        raise CommandError(
                            "public AI model count does not match migration"
                        )
                    for route in [
                        "channels",
                        "conversations",
                        "memories",
                        "sandbox",
                        "agent-jobs",
                        "workflow-runs",
                        "space/meta",
                        "space/profiles",
                        "space/templates",
                        "space/jobs",
                        "space/assets",
                    ]:
                        _request(3000, "/api/ai/" + route)
                    for route in [
                        "models",
                        "chat",
                        "memories",
                        "agent-jobs",
                        "workflow-runs",
                        "space/jobs",
                    ]:
                        _request(
                            3000,
                            "/api/ai/" + route,
                            400,
                            method="POST",
                            payload={},
                            origin="http://127.0.0.1:3000",
                        )
                    _request(
                        3000,
                        "/api/ai/models",
                        403,
                        method="POST",
                        payload={},
                        origin="https://cross-origin.invalid",
                    )
                    _request(
                        8111,
                        "/api/ai/conversations",
                        403,
                        signed_email="cutover-unknown@example.invalid",
                    )
                    _request(8111, "/api/ai/conversations", 401)
                    for port in [
                        8001,
                        8002,
                        8011,
                        8012,
                        8021,
                        8022,
                        8031,
                        8032,
                        8041,
                        8042,
                        8051,
                        8052,
                        8061,
                        8062,
                        8071,
                        8072,
                        8081,
                        8091,
                        8092,
                        8101,
                        8102,
                    ]:
                        _request(port, "/health/ready")
                    release = Path(options["release_root"])
                    snapshot = release / "source-snapshot"
                    paths = [
                        *sorted((snapshot / "lib/ai").rglob("*.ts")),
                        snapshot / "lib/django/ai-service.ts",
                        *sorted((snapshot / "app/api/ai").rglob("*.ts")),
                        snapshot / "worker/index.ts",
                    ]
                    if len(paths) < 40 or any(
                        not item.is_file() or item.is_symlink() for item in paths
                    ):
                        raise CommandError("deployed AI source snapshot incomplete")
                    forbidden = [
                        "lib/database/d1",
                        "getD1Database",
                        "ensureAi",
                        "CREATE TABLE",
                        "tests/legacy",
                    ]
                    ai_paths = [
                        item for item in paths if item != snapshot / "worker/index.ts"
                    ]
                    if any(
                        any(
                            word in item.read_text(encoding="utf-8")
                            for word in forbidden
                        )
                        for item in ai_paths
                    ):
                        raise CommandError("legacy AI domain source reachable")
                    worker = (snapshot / "worker/index.ts").read_text(encoding="utf-8")
                    if "wakeAiQueue" not in worker or any(
                        word in worker
                        for word in [
                            "runAiAgentQueue",
                            "runAiWorkflowQueue",
                            "runAiSpaceQueue",
                        ]
                    ):
                        raise CommandError("AI scheduled route has not cut over")
                    result = {
                        "version": "ai-assistant-system-test-receipt-v1",
                        "status": "passed",
                        "cutoverId": cutover,
                        "migrationRunId": run_id,
                        "sourceDigest": run.source_snapshot_digest,
                        "targetDigest": run.target_snapshot_digest,
                        "authorityEpoch": str(authority.authority_epoch),
                        "effectiveWorkerHead": effective,
                        "deployedSourceSha256": sha256_json(
                            {
                                str(item.relative_to(snapshot)): _sha(item.read_bytes())
                                for item in paths
                            }
                        ),
                        "checks": {name: "passed" for name in sorted(CHECKS)},
                        "r2": "retained-private-ai-space-bytes-postgresql-metadata-only",
                        "recordedAt": timezone.now().isoformat(),
                    }
                    if _effective_worker_head() != effective:
                        raise CommandError("Worker effective head changed during smoke")
                else:
                    smoke_path = Path(options["smoke_receipt"])
                    if (
                        not smoke_path.is_file()
                        or smoke_path.is_symlink()
                        or smoke_path.stat().st_size > 65536
                    ):
                        raise CommandError("invalid smoke receipt")
                    raw_smoke = smoke_path.read_bytes()
                    smoke = json.loads(raw_smoke)
                    age = timezone.now() - timezone.datetime.fromisoformat(
                        smoke["recordedAt"]
                    )
                    if (
                        smoke.get("version") != "ai-assistant-system-test-receipt-v1"
                        or smoke.get("status") != "passed"
                        or smoke.get("cutoverId") != cutover
                        or smoke.get("migrationRunId") != run_id
                        or smoke.get("sourceDigest") != run.source_snapshot_digest
                        or smoke.get("targetDigest") != run.source_snapshot_digest
                        or smoke.get("authorityEpoch") != str(authority.authority_epoch)
                        or smoke.get("checks") != {name: "passed" for name in CHECKS}
                        or smoke.get("effectiveWorkerHead") != _effective_worker_head()
                        or not -120 <= age.total_seconds() <= 1800
                    ):
                        raise CommandError("incomplete/stale smoke receipt")
                    raw, statements = _sql("0114_ai_domain_retirement.sql")
                    plan = {
                        "cutoverId": cutover,
                        "migrationRunId": run_id,
                        "authorityEpoch": str(authority.authority_epoch),
                        "sourceDigest": run.source_snapshot_digest,
                        "smokeSha256": _sha(raw_smoke),
                        "migrationSha256": _sha(raw),
                        "preservedSha256": before,
                    }
                    plan_id = sha256_json(plan)
                    result = {**plan, "planId": plan_id, "status": "planned"}
                    if action == "retirement-apply":
                        if options["approved_plan_id"] != plan_id:
                            raise CommandError("retirement plan CAS mismatch")
                        _write(output.with_suffix(".intent.json"), result)
                        connection.execute(statements[0])
                        connection.execute(
                            "INSERT INTO domain_retirement_receipts(domain,version,status,cutover_id,plan_id,attestation_sha256,smoke_receipt_sha256,preflight_evidence_sha256,migration_sha256,audit_id,preserved_evidence_sha256,created_at,completed_at) VALUES ('ai-assistant','ai-assistant-domain-retirement-receipt-v1','approved',?,?,?,?,?,?,?,?,?,NULL)",
                            (
                                cutover,
                                plan_id,
                                sha256_json(
                                    {
                                        "epoch": str(authority.authority_epoch),
                                        "run": run_id,
                                        "digest": run.source_snapshot_digest,
                                    }
                                ),
                                _sha(raw_smoke),
                                plan_id,
                                _sha(raw),
                                plan_id,
                                before,
                                timezone.now().isoformat(),
                            ),
                        )
                        for statement in statements[1:]:
                            connection.execute(statement)
                        if _preserved(connection) != before:
                            raise CommandError("retirement changed other-domain facts")
                        _source_rejects(connection)
                        for table in (*HISTORICAL_MODELS, "ai_write_authority"):
                            if (
                                connection.execute(
                                    "SELECT type FROM sqlite_master WHERE name=?",
                                    (table,),
                                ).fetchone()
                                != ("view",)
                                or connection.execute(
                                    f"SELECT COUNT(*) FROM {table}"
                                ).fetchone()[0]
                                != 0
                            ):
                                raise CommandError("terminal tombstone missing")
                        if (
                            connection.execute(
                                "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'ai_%_retired_%_guard'"
                            ).fetchone()[0]
                            != 120
                        ):
                            raise CommandError("terminal guards incomplete")
                        connection.commit()
                        result.update(
                            status="retired", tombstoneViews=40, permanentGuards=120
                        )
                    else:
                        connection.rollback()
            _write(output, result)
            self.stdout.write(
                json.dumps(
                    {
                        **result,
                        "evidencePath": str(output),
                        "evidenceSha256": _sha(output.read_bytes()),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        finally:
            connection.close()

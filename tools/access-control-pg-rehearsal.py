"""Isolated PostgreSQL rehearsal; never accepts a production port/database."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
import secrets
import importlib
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
url = os.environ.get("TERUISI_DJANGO_DATABASE_URL", "")
address = urlsplit(url)
if address.hostname != "127.0.0.1" or not 55432 <= (address.port or 0) <= 55999 or not address.path.startswith("/teruisi_access_control_rehearsal_"):
    raise RuntimeError("isolated rehearsal connection required")
os.environ["TERUISI_DJANGO_ENVIRONMENT"] = "test"
os.environ["TERUISI_DJANGO_PROCESS_ROLE"] = "development"
os.environ["DJANGO_SETTINGS_MODULE"] = "teruisi_backend.settings"
os.environ["DJANGO_SECRET_KEY"] = secrets.token_hex(32)
os.environ["TERUISI_DJANGO_INTERNAL_SECRET"] = "isolated-access-rehearsal-secret-not-production"
sys.path.insert(0, str(ROOT / "backend"))

import django
django.setup()
from django.core.management import call_command
from django.db import connection, transaction
from django.test import Client, override_settings
from access_control.models import AccessControlWriteAuthority, AppUser, PermissionAuditEvent
from access_control.tests.test_migration import _create_source, _command, _statements
from sales.tests.factories import signed_headers

call_command("migrate", verbosity=0)
if AccessControlWriteAuthority.objects.get(id=1).status != "d1":
    raise RuntimeError("use a fresh rehearsal database")
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
    source = Path(directory) / "source.sqlite"
    _create_source(source)
    dry = _command("migrate_access_control_from_d1", source=str(source), mode="dry-run")
    applied = _command("migrate_access_control_from_d1", source=str(source), mode="apply", approve_run_id=dry["runId"])
    verified = _command("migrate_access_control_from_d1", source=str(source), mode="verify-only")
    import sqlite3
    with sqlite3.connect(source) as d1:
        for statement in _statements("0111_access_control_write_authority.sql"):
            d1.execute(statement)
    cutover = "access-control-isolated-rehearsal"
    _command("access_control_write_authority", source=str(source), prepare=True, approved_run_id=applied["runId"], cutover_id=cutover)
    activation = _command("access_control_write_authority", source=str(source), activate=True, approved_run_id=applied["runId"], cutover_id=cutover)

# Execute the exact production provisioning program against this isolated cluster.
provision_source = (ROOT / "tools/django-access-control.ps1").read_text(encoding="utf-8-sig")
code = re.search(r"\$code = @'\r?\n(.*?)\r?\n'@", provision_source, re.S).group(1)
os.environ["TERUISI_PROVISION_DATABASE_URL"] = url
os.environ["TERUISI_PROVISION_ACCESS_CONTROL_READER_PASSWORD"] = "isolated-reader-not-production-20260905"
os.environ["TERUISI_PROVISION_ACCESS_CONTROL_WRITER_PASSWORD"] = "isolated-writer-not-production-20260905"
exec(compile(code, "exact-production-provisioning", "exec"), {})
client = Client()
checks = []

def request(method, path, body=None, *, expected=200, email="dengweizhang321@gmail.com", role="admin", request_id="pg-rehearsal"):
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode() if body is not None else b""
    headers = signed_headers(path, method=method, body=raw, email=email, role=role, request_id=request_id)
    # Factory uses TEST_SECRET; use that only inside the isolated process.
    response = client.generic(method, path, data=raw, content_type="application/json", headers=headers)
    if response.status_code != expected:
        raise RuntimeError(f"{method} {path}: expected {expected}, got {response.status_code}: {response.content!r}")
    checks.append(f"{method}:{expected}")
    return response

from sales.tests.factories import TEST_SECRET
os.environ["TERUISI_DJANGO_INTERNAL_SECRET"] = TEST_SECRET
with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE teruisi_access_control_writer")
    with override_settings(DJANGO_PROCESS_ROLE="access_control_writer", ACCESS_CONTROL_WRITE_AUTHORITY_EPOCH=activation["authorityEpoch"], ACCESS_CONTROL_WRITE_CUTOVER_ID=cutover):
        request("GET", "/health/ready")
        payload = {"email": "pg-write@example.test", "displayName": "镜像测试", "role": "analyst", "status": "active", "scope": None, "reason": "隔离镜像验证"}
        first = request("POST", "/api/access-control/users", payload, expected=201)
        replay = request("POST", "/api/access-control/users", payload, expected=201)
        assert replay["X-Teruisi-Write-Replay"] == "1"
        updated = {**payload, "role": "viewer", "expectedVersion": 1}
        request("PUT", "/api/access-control/users", updated, request_id="pg-update")
        request("PUT", "/api/access-control/users", updated, expected=409, request_id="pg-stale")
        request("POST", "/api/access-control/principal/authorize-background", {"ownerEmail": payload["email"], "scope": None}, expected=404, email=payload["email"], role="viewer")
        request("GET", "/api/access-control/users", email="local-admin@teruisi.local")
        assert PermissionAuditEvent.objects.filter(target_email=payload["email"]).count() == 2
        with override_settings(ACCESS_CONTROL_WRITE_AUTHORITY_EPOCH="00000000-0000-0000-0000-000000000000"):
            request("POST", "/api/access-control/users", {**payload, "email": "fenced@example.test"}, expected=503, request_id="pg-fenced")
    transaction.set_rollback(True)

connection.close()
with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute("SET LOCAL ROLE teruisi_access_control_reader")
    with override_settings(DJANGO_PROCESS_ROLE="access_control_reader", DJANGO_EXPECT_READ_ONLY=True):
        import access_control.urls
        import teruisi_backend.urls
        from django.urls import clear_url_caches
        importlib.reload(access_control.urls)
        importlib.reload(teruisi_backend.urls)
        clear_url_caches()
        request("GET", "/health/ready")
        request("GET", "/api/access-control/roles")
        request("GET", "/api/access-control/users", expected=403, email="unknown@example.test")
        request("POST", "/api/access-control/principal/authorize-background", {"ownerEmail": "unknown@example.test", "scope": None}, expected=403, email="unknown@example.test", role="viewer")
    transaction.set_rollback(True)
print(json.dumps({"status": "passed", "checks": checks, "dryRun": dry["runId"], "applyRun": applied["runId"], "verifyRun": verified["runId"], "sourceDigest": applied["sourceDigest"], "targetDigest": applied["targetDigest"], "database": address.path[1:]}, separators=(",", ":")))

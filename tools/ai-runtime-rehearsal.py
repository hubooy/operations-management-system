"""Exercise independent AI database roles and real reader/writer HTTP processes.

Only the parent isolated rehearsal may invoke this. No production port, key,
provider endpoint, callback or browser automation is used.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--run-root", required=True)
parser.add_argument("--apply-run", required=True)
args = parser.parse_args()
run_root = Path(args.run_root).resolve()
db = urlsplit(os.environ["TERUISI_DJANGO_DATABASE_URL"])
if (
    not run_root.is_relative_to((ROOT / ".runtime").resolve())
    or db.hostname != "127.0.0.1"
    or db.port != 55443
    or db.path != "/teruisi_ai_rehearsal"
    or os.environ.get("TERUISI_DJANGO_ENVIRONMENT") != "test"
):
    raise RuntimeError("AI system rehearsal only accepts the independent test cluster")
sys.path.insert(0, str(ROOT / "backend"))
import django

django.setup()
import psycopg
from django.core.management import call_command
from ai_assistant.database_contract import provision
from sales.auth import Principal

source = run_root / "authority-source.sqlite"
source.write_bytes((ROOT / ".runtime/ai-source-rehearsal.sqlite").read_bytes())
call_command(
    "ai_cutover_check",
    action="install-authority",
    source=str(source),
    output=str(run_root / "authority-install.json"),
)
for action in ("prepare", "activate"):
    call_command(
        "ai_write_authority",
        source=str(source),
        approved_run_id=args.apply_run,
        cutover_id="ai-isolated-system-cutover",
        **{action: True},
    )
from ai_assistant.control_models import AiWriteAuthority

authority = AiWriteAuthority.objects.get(id=1)
reader_password, writer_password = secrets.token_hex(32), secrets.token_hex(32)
with psycopg.connect(os.environ["TERUISI_DJANGO_DATABASE_URL"]) as owner:
    provision(owner, reader_password, writer_password)
role_envs = {}
for role, password in (("reader", reader_password), ("writer", writer_password)):
    url = urlunsplit(
        db._replace(netloc=f"teruisi_ai_{role}:{password}@127.0.0.1:55443")
    )
    role_envs[role] = {
        **os.environ,
        "TERUISI_DJANGO_DATABASE_URL": url,
        "TERUISI_DJANGO_PROCESS_ROLE": "ai_" + role,
        "TERUISI_DJANGO_EXPECT_READ_ONLY": str(role == "reader").lower(),
        "TERUISI_DJANGO_AI_AUTHORITY_EPOCH": str(authority.authority_epoch),
        "TERUISI_DJANGO_AI_CUTOVER_ID": authority.cutover_id,
    }
    with psycopg.connect(url, autocommit=True) as connection:
        for sql in [
            "UPDATE ai_models SET name=name",
            "DELETE FROM ai_tool_audit_logs",
            "SELECT * FROM sales_order_lines LIMIT 1",
            "UPDATE ai_write_authority SET status='d1'",
            "CREATE TABLE public.ai_privilege_escape(id int)",
        ]:
            try:
                connection.execute(sql)
            except psycopg.Error:
                pass
            else:
                raise RuntimeError(
                    "AI minimal role unexpectedly accepted a forbidden operation"
                )

principal = Principal(
    "local-admin@teruisi.local", "Isolated administrator", "admin", None
)


def request(
    port,
    path,
    *,
    method="GET",
    payload=None,
    actor=principal,
    request_id=None,
    tamper=False,
):
    body = (
        ""
        if payload is None
        else json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )
    route, _, query = path.partition("?")
    stamp = str(int(time.time()))
    identity = (
        base64.urlsafe_b64encode(
            json.dumps(
                dict(
                    email=actor.email,
                    displayName=actor.display_name,
                    role=actor.role,
                    scope=actor.scope,
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    request_id = request_id or secrets.token_hex(16)
    digest = hashlib.sha256(body.encode()).hexdigest()
    canonical = "\n".join(
        ["v1", stamp, request_id, method, route, query, digest, identity]
    )
    signature = hmac.new(
        os.environ["TERUISI_DJANGO_INTERNAL_SECRET"].encode(),
        canonical.encode(),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Teruisi-Principal": identity,
        "X-Teruisi-Timestamp": stamp,
        "X-Teruisi-Request-Id": request_id,
        "X-Teruisi-Content-SHA256": digest,
        "X-Teruisi-Signature": "v1=" + signature,
    }
    if tamper:
        headers["X-Teruisi-Signature"] = "v1=" + "0" * 64
    call = Request(
        f"http://127.0.0.1:{port}{path}",
        data=body.encode() if payload is not None else None,
        method=method,
        headers=headers,
    )
    try:
        response = urlopen(call, timeout=15)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response), dict(response.headers)


children = []
try:
    for role, port, threads in (("reader", 18111, 2), ("writer", 18112, 6)):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
        log = (run_root / (role + "-http.log")).open("wb")
        child = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "waitress",
                "--host=127.0.0.1",
                f"--port={port}",
                f"--threads={threads}",
                "teruisi_backend.wsgi:application",
            ],
            cwd=ROOT / "backend",
            env=role_envs[role],
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        children.append((child, log))
        for _ in range(100):
            if child.poll() is not None:
                raise RuntimeError("Isolated AI process exited")
            try:
                with urlopen(
                    f"http://127.0.0.1:{port}/health/ready", timeout=2
                ) as ready:
                    assert json.load(ready)["status"] == "ready"
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError(
                "Independent AI role readiness failed; inspect isolated process log"
            )
    checks = []

    def expect(name, expected, *pos, **kw):
        status, result, headers = request(*pos, **kw)
        if status != expected:
            raise RuntimeError(
                f"{name}: expected {expected}, got {status}, code={result.get('code', '')}"
            )
        checks.append(name)
        return result, headers

    for path in (
        "/api/ai/conversations",
        "/api/ai/models",
        "/api/ai/channels",
        "/api/ai/memories",
        "/api/ai/space/meta",
        "/api/ai/space/jobs",
        "/api/ai/space/assets",
        "/api/ai/agent-jobs",
        "/api/ai/workflow-runs",
    ):
        result, _ = expect("reader:" + path, 200, 18111, path)
        assert "api_key_encrypted" not in json.dumps(result)
    expect("writer-refuses-reader-route", 403, 18112, "/api/ai/models")
    expect(
        "reader-refuses-mutation",
        403,
        18111,
        "/api/ai/memories",
        method="POST",
        payload={},
    )
    expect("tampered-signature", 401, 18111, "/api/ai/models", tamper=True)
    expect(
        "unknown-user",
        403,
        18111,
        "/api/ai/conversations",
        actor=Principal("unknown@example.invalid", "Unknown", "admin", None),
    )
    expect("duplicate-query", 400, 18111, "/api/ai/conversations?page=1&page=2")
    expect("invalid-write", 400, 18112, "/api/ai/memories", method="POST", payload={})
    result, _ = expect(
        "new-topic-without-paid-provider",
        200,
        18112,
        "/api/ai/chat",
        method="POST",
        payload={"clientRequestId": "isolated-chat-newtopic", "message": "新话题"},
    )
    replay, _ = expect(
        "durable-chat-replay",
        200,
        18112,
        "/api/ai/chat",
        method="POST",
        payload={"clientRequestId": "isolated-chat-newtopic", "message": "新话题"},
    )
    assert result == replay
    expect(
        "replay-conflict",
        409,
        18112,
        "/api/ai/chat",
        method="POST",
        payload={"clientRequestId": "isolated-chat-newtopic", "message": "other"},
    )
    expect(
        "reader-message-page",
        200,
        18111,
        "/api/ai/chat?conversationId=" + result["conversationId"],
    )
    # A real minimum-privilege writer persists a synthetic PNG. Only provider and
    # edge background authorization are substitutes; bytes never cross the edge.
    image_fixture = r"""
import base64,json
from types import SimpleNamespace
from unittest.mock import patch
import django
django.setup()
from ai_assistant.tests import AiDomainTests
from ai_assistant import models as m, space
from ai_assistant.policy import mutation
from sales.auth import Principal
principal=Principal("local-admin@teruisi.local","Isolated administrator","admin",None)
raw=AiDomainTests.image_bytes(None)
with mutation(principal):
    AiDomainTests.image_job(SimpleNamespace(owner=principal),number=987)
def edge(action,payload,actor):
    if action != "authorize_background": raise AssertionError("Storage edge is retired")
    return {"ok":True,"principal":{"email":principal.email,"displayName":principal.display_name,"role":"admin","scope":None}}
with patch.object(space,"decrypt",return_value="synthetic-no-provider"), patch.object(space.transport,"edge",side_effect=edge), patch.object(space.transport,"bounded_json",return_value={"data":[{"b64_json":base64.b64encode(raw).decode()}]}):
    assert space.tick()["status"]=="succeeded"
asset=m.AiSpaceAssets.objects.get()
assert bytes(m.AiSpaceAssetPayload.objects.get(asset=asset).content)==raw
print(json.dumps({"id":asset.id,"sha256":asset.content_sha256,"byteSize":len(raw)}))
"""
    fixture = subprocess.run([sys.executable, "-c", image_fixture], cwd=ROOT / "backend", env=role_envs["writer"], capture_output=True, text=True, timeout=45)
    if fixture.returncode:
        raise RuntimeError("Isolated minimum-role image fixture failed: " + fixture.stderr[-3000:])
    image = json.loads(fixture.stdout)
    content, _ = expect("postgres-image-download", 200, 18111, "/api/ai/space/assets/" + image["id"] + "/content")
    downloaded = base64.b64decode(content["base64"], validate=True)
    assert len(downloaded) == image["byteSize"] and hashlib.sha256(downloaded).hexdigest() == image["sha256"]
    expect("postgres-image-unknown-owner", 403, 18111, "/api/ai/space/assets/" + image["id"] + "/content", actor=Principal("unknown@example.invalid", "Unknown", "admin", None))
    report = {
        "status": "passed",
        "productionWrites": False,
        "externalCalls": 0,
        "databasePort": 55443,
        "readerPort": 18111,
        "writerPort": 18112,
        "negativeDatabasePrivileges": 10,
        "checks": checks,
        "postgresImage": image,
    }
    (run_root / "system-result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
finally:
    for child, log in reversed(children):
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=10)
        log.close()

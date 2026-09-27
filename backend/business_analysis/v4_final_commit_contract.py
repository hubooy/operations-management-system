"""Pure, non-authoritative request fingerprint for a future v4 seal commit.

The caller must obtain the actor, directory, current claim, and key identity
from protected sources. None of those authorities can be inferred here.
"""
from __future__ import annotations

import hashlib
import hmac
import re

from . import evidence_seal_v4
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, digest


OPERATION = "commit-seal-v1"
# Keep byte-for-byte compatibility with ai_assistant.business_v4_seal_hmac.
PARENT_SEAL_PURPOSE = b"teruisi:business-v4:parent-seal:v1\x00"
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX16 = re.compile(r"[0-9a-f]{16}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")


def derive_parent_seal_key(master_secret):
    """Test/provisioning helper; never pass the master to a sealer runtime."""
    if type(master_secret) is not str or len(master_secret) < 32:
        raise AnalysisContractError("v4父封存派生主密钥无效")
    return hmac.new(master_secret.encode("utf-8"), PARENT_SEAL_PURPOSE,
                    hashlib.sha256).digest()


def _validated_body(body_json, body_mac, derived_key, key_id):
    if (type(derived_key) is not bytes or len(derived_key) != 32
            or type(body_mac) is not str or HEX64.fullmatch(body_mac) is None
            or type(key_id) is not str or HEX16.fullmatch(key_id) is None):
        raise AnalysisContractError("v4父封存MAC输入或密钥版本无效")
    body = evidence_seal_v4.read(body_json)
    if body["keyId"] != key_id:
        raise AnalysisContractError("v4父封存正文密钥版本不一致")
    encoded = body_json.encode("utf-8")
    expected = hmac.new(derived_key, encoded, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, body_mac):
        raise AnalysisContractError("v4父封存正文MAC验证失败")
    return body, hashlib.sha256(encoded).hexdigest()


def verify_parent_seal_body(body_json, body_mac, derived_key, key_id):
    """Verify exact canonical UTF-8 body bytes; return only their SHA-256."""
    return _validated_body(body_json, body_mac, derived_key, key_id)[1]


def commit_seal_request_digest(*, run_id, attempt_id, actor_email,
                               actor_version, parent_version, plan_digest,
                               directory_digest, body_json, body_mac,
                               key_id, derived_key):
    """Fingerprint one checked commit intent; no ticket/role/DB side effect.

    This digest is not an authorization token. The future protected caller
    must independently check its current claim, actor, source roots, revision
    cutover and same-transaction seal/consumption requirements.
    """
    body, body_digest = _validated_body(body_json, body_mac, derived_key, key_id)
    if (type(run_id) is not str or IDENTIFIER.fullmatch(run_id) is None
            or type(attempt_id) is not str or IDENTIFIER.fullmatch(attempt_id) is None
            or type(actor_email) is not str or not actor_email
            or len(actor_email) > 320 or actor_email != actor_email.strip()
            or any(ord(char) < 32 for char in actor_email)
            or type(actor_version) is not int
            or not 1 <= actor_version <= MAX_SAFE_INTEGER
            or type(parent_version) is not int
            or not 1 <= parent_version < MAX_SAFE_INTEGER
            or type(plan_digest) is not str or HEX64.fullmatch(plan_digest) is None
            or type(directory_digest) is not str
            or HEX64.fullmatch(directory_digest) is None):
        raise AnalysisContractError("v4父封存请求身份或摘要无效")
    if (body["runId"] != run_id or body["attemptId"] != attempt_id
            or body["actorVersion"] != actor_version
            or body["evidenceVersion"] != parent_version + 1
            or body["planDigest"] != plan_digest
            or body["directoryDigest"] != directory_digest):
        raise AnalysisContractError("v4父封存请求与规范正文不一致")
    return digest({"operation": OPERATION, "runId": run_id,
        "attemptId": attempt_id, "actorEmail": actor_email,
        "actorVersion": actor_version, "parentVersion": parent_version,
        "planDigest": plan_digest, "directoryDigest": directory_digest,
        "bodyDigest": body_digest, "bodyMac": body_mac, "keyId": key_id})

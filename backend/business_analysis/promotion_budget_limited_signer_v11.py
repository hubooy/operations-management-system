"""Isolated 0067/0070 signer bridge; never a v11 publication authority.

0070's ticket reader returns the old 0067 proof, while 0073 writes a separate
proof table. The 0068 receipt format does not bind a ticket or claim id. This
module proves a bounded read and full-byte reconstruction path with a
synthetic key, but deliberately refuses normal signing until a versioned SQL
receipt/claim contract exists. No credential or production key loader exists.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Protocol

from .contracts import canonical
from . import promotion_budget_verifier_receipt_v11 as receipt


SIGN_ROLE = "teruisi_ai_budget_v11_sign_login"
SCHEMA = "budget-v11-proof-read-v2"
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
READ_SQL = "SELECT public.ai_budget_v11_read_proof_ticket_v2(%s,%s,%s,%s)"
MAX_SECONDS = 600


class SignerBlocked(RuntimeError):
    """A fixed, non-sensitive denial at the isolated signer boundary."""


class NotConfigured(SignerBlocked):
    """There is intentionally no production key provider."""


class KeyProvider(Protocol):
    def get_key(self, key_id: str) -> bytes: ...


class _NoKeyProvider:
    def get_key(self, key_id: str) -> bytes:
        raise NotConfigured("protected signer key provider is not configured")


DEFAULT_KEY_PROVIDER: KeyProvider = _NoKeyProvider()


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise SignerBlocked(reason)


def _isolated_identity(value, role: str, port: int) -> None:
    _need(type(value) is tuple and len(value) == 6,
        "isolated session identity shape drift")
    _need(value[:3] == (role, role, False),
        "isolated session role drift")
    _need(value[3] == "test_teruisi_ai_rehearsal",
        "isolated session database drift")
    _need(value[4] in ("127.0.0.1", "127.0.0.1/32", "::1", "::1/128"),
        "isolated session address drift")
    _need(value[5] == port, "isolated session port drift")


@dataclass(frozen=True)
class ClaimedProof:
    ticket_id: str
    claim_id: str
    run_id: str
    attempt: int
    run_version: int
    report_id: str
    owner_email: str
    binding_digest: str
    attestation_id: str
    attestation_sha256: str
    attestation_text: str

    @classmethod
    def from_result(cls, value: object, *, ticket_id: str, run_id: str,
                    attempt: int, attestation_sha256: str) -> "ClaimedProof":
        _need(type(value) is dict and set(value) == {
            "schemaVersion", "ticketId", "claimId", "runId", "attempt",
            "runVersion", "reportId", "ownerEmail", "bindingDigest",
            "attestationId", "attestationSha256", "attestationText",
            "readyAuthorized"}, "ticket result shape changed")
        _need(value["schemaVersion"] == SCHEMA and
            value["readyAuthorized"] is False and
            value["ticketId"] == ticket_id and
            value["runId"] == run_id and
            value["attempt"] == attempt and
            value["attestationSha256"] == attestation_sha256 and
            type(value["claimId"]) is str and
            UUID.fullmatch(value["claimId"]) is not None and
            type(value["runVersion"]) is int and value["runVersion"] >= 1 and
            type(value["reportId"]) is str and
            1 <= len(value["reportId"]) <= 160 and
            type(value["ownerEmail"]) is str and
            1 <= len(value["ownerEmail"]) <= 320 and
            type(value["bindingDigest"]) is str and
            HEX64.fullmatch(value["bindingDigest"]) is not None and
            type(value["attestationId"]) is str and
            HEX64.fullmatch(value["attestationId"]) is not None and
            type(value["attestationText"]) is str and
            1 <= len(value["attestationText"].encode("utf-8")) <= 131072 and
            hashlib.sha256(value["attestationText"].encode("utf-8")
                ).hexdigest() == attestation_sha256,
            "ticket result identity or proof changed")
        try:
            body = json.loads(value["attestationText"])
            _need(canonical(body) == value["attestationText"] and
                body["runId"] == run_id and body["attempt"] == attempt and
                body["runVersion"] == value["runVersion"] and
                body["bindingDigest"] == value["bindingDigest"],
                "ticket proof body changed")
        except (KeyError, TypeError, ValueError, UnicodeError, RecursionError):
            raise SignerBlocked("ticket proof body invalid") from None
        return cls(ticket_id, value["claimId"], run_id, attempt,
            value["runVersion"], value["reportId"], value["ownerEmail"],
            value["bindingDigest"], value["attestationId"],
            attestation_sha256, value["attestationText"])


class BoundedRunReader:
    """Rebuild bytes through the existing owning preflight on a separate DB role.

    This is intentionally concrete: callers cannot supply digest fields or a
    callback that pretends to have parsed HTML/XLSX. The owning Django
    connection must be a distinct non-superuser reader; it is never the
    sign_login, Web writer or key owner connection.
    """

    def __init__(self, principal, *, expected_port: int,
                 max_seconds: int = MAX_SECONDS):
        _need(type(max_seconds) is int and 1 <= max_seconds <= MAX_SECONDS,
            "reader time bound invalid")
        _need(type(expected_port) is int and 55440 <= expected_port <= 55999,
            "reader port is not isolated")
        self.principal = principal
        self.expected_port = expected_port
        self.max_seconds = max_seconds

    def _identity(self):
        from django.conf import settings
        from django.db import connection
        _need(settings.DJANGO_PROCESS_ROLE == "ai_reader" and
            settings.DJANGO_EXPECT_READ_ONLY is True,
            "owning reader process role drift")
        _need(not connection.in_atomic_block, "reader transaction is open")
        with connection.cursor() as cursor:
            cursor.execute("SELECT session_user,current_user,"
                "(SELECT rolsuper FROM pg_catalog.pg_roles "
                "WHERE rolname=session_user),current_database(),"
                "COALESCE(inet_server_addr()::text,''),inet_server_port()")
            return cursor.fetchone()

    def _load_run(self, run_id):
        from ai_assistant import business_files as files
        return files.get(run_id, self.principal)

    def _prepare_full(self, run_id, checkpoint):
        from ai_assistant import business_promotion_budget_v11_preflight as preflight
        return preflight.prepare(run_id, self.principal,
            enabled=True, checkpoint=checkpoint)

    def rebuild(self, claim: ClaimedProof) -> dict:
        self._stage = "identity"
        identity = self._identity()
        _isolated_identity(identity, "teruisi_ai_reader", self.expected_port)
        started = time.monotonic()

        def checkpoint(*_args, **_kwargs):
            _need(time.monotonic() - started <= self.max_seconds,
                "owning reader time limit exceeded")

        def exact_row():
            row = self._load_run(claim.run_id)
            _need(row.id == claim.run_id and
                row.attempt == claim.attempt and
                row.version == claim.run_version and
                row.report_id == claim.report_id and
                row.owner_email == claim.owner_email and
                row.binding_digest == claim.binding_digest and
                row.renderer_version == 11 and row.status == "paused" and
                row.error_code == "renderer_unpublished" and
                row.draft is False, "owning run identity changed")

        self._stage = "initial_row"
        exact_row()
        checkpoint()
        self._stage = "full_bytes"
        fresh = self._prepare_full(claim.run_id, checkpoint)
        checkpoint()
        self._stage = "final_row"
        exact_row()
        self._stage = "proof_compare"
        _need(type(fresh) is dict and
            fresh.get("schemaVersion") ==
                "business-promotion-budget-v11-owning-preflight-v1" and
            fresh.get("candidateOnly") is True and
            fresh.get("readyAuthorized") is False and
            fresh.get("databaseCanIndependentlyVerifyProcessAssertions") is False and
            fresh.get("runId") == claim.run_id and
            fresh.get("attempt") == claim.attempt and
            fresh.get("runVersion") == claim.run_version and
            fresh.get("bindingDigest") == claim.binding_digest and
            fresh.get("attestationSha256") == claim.attestation_sha256 and
            fresh.get("attestationText") == claim.attestation_text,
            "owning full-byte proof changed")
        return fresh


def _after_claim_reason(error: Exception, phase: str,
                        reader: BoundedRunReader) -> str:
    if phase == "reader":
        item = error
        for _ in range(4):
            if getattr(item, "sqlstate", None) == "42501":
                return "owning_reader_acl_denied"
            item = getattr(item, "__cause__", None)
            if item is None:
                break
        stage = getattr(reader, "_stage", "unknown")
        if stage in {"identity", "initial_row", "full_bytes",
                "final_row", "proof_compare"}:
            return "owning_" + stage + "_failed"
        return "owning_unknown_failed"
    return {"claim_parse":"claim_result_invalid",
        "receipt_body":"receipt_candidate_invalid",
        "receipt_mac":"hmac_candidate_failed"}.get(phase,
        "after_claim_unclassified")


def sign_once(sign_db, reader: BoundedRunReader, *, ticket_id: str,
              run_id: str, attempt: int, attestation_sha256: str,
              key_id: str, key_provider: KeyProvider = DEFAULT_KEY_PROVIDER,
              isolated_candidate: bool = False) -> dict:
    """Consume one 0070 ticket, then sign a freshly rebuilt old 0067 proof.

    v1 MAC does not contain claimId, so normal calls are refused before key
    access or ticket consumption. The isolated path is not a publish API.
    """
    _need(isolated_candidate is True,
        "ticket-bound protected receipt version is not installed")
    _need(os.getenv("TERUISI_DJANGO_ENVIRONMENT") == "test",
        "signer is isolated-test only")
    _need(type(ticket_id) is str and UUID.fullmatch(ticket_id) is not None and
        type(run_id) is str and RUN_ID.fullmatch(run_id) is not None and
        type(attempt) is int and 1 <= attempt <= 5 and
        type(attestation_sha256) is str and
        HEX64.fullmatch(attestation_sha256) is not None and
        type(key_id) is str and receipt.KEY_ID.fullmatch(key_id) is not None,
        "signer request shape invalid")
    _need(getattr(sign_db, "autocommit", None) is True,
        "signer requires autocommit")
    _need(type(reader) is BoundedRunReader,
        "signer requires concrete owning reader")
    secret = key_provider.get_key(key_id)
    _need(type(secret) is bytes and 32 <= len(secret) <= 128,
        "signer key shape invalid")
    with sign_db.cursor() as cursor:
        cursor.execute("SELECT session_user,current_user,"
            "(SELECT rolsuper FROM pg_catalog.pg_roles "
            "WHERE rolname=session_user),current_database(),"
            "COALESCE(inet_server_addr()::text,''),inet_server_port()")
        identity = cursor.fetchone()
        _isolated_identity(identity, SIGN_ROLE, reader.expected_port)
    try:
        with sign_db.cursor() as cursor:
            cursor.execute(READ_SQL,
                [ticket_id, run_id, attempt, attestation_sha256])
            rows = cursor.fetchmany(2)
    except Exception:
        return {"status": "unknown_ticket_claim", "readyAuthorized": False,
            "releaseAllowed": False, "retryAllowed": False,
            "ticketBoundInMac": False}
    try:
        phase = "claim_parse"
        _need(len(rows) == 1, "ticket claim result changed")
        claim = ClaimedProof.from_result(rows[0][0], ticket_id=ticket_id,
            run_id=run_id, attempt=attempt,
            attestation_sha256=attestation_sha256)
        phase = "reader"
        reader.rebuild(claim)
        phase = "receipt_body"
        value = receipt.body(key_id=key_id, run_id=run_id, attempt=attempt,
            report_id=claim.report_id, owner_email=claim.owner_email,
            attestation_id=claim.attestation_id,
            attestation_text=claim.attestation_text)
        raw = canonical(value)
        _need(len(raw.encode("utf-8")) <= 262144,
            "signer receipt too large")
        phase = "receipt_mac"
        mac = receipt._mac(secret, raw)
    except Exception as error:
        return {"status": "unknown_after_ticket_claim",
            "readyAuthorized": False, "releaseAllowed": False,
            "retryAllowed": False, "ticketBoundInMac": False,
            "reasonCode": _after_claim_reason(error, phase, reader)}
    return {"status": "signed_candidate_unpublished",
        "receiptText": raw, "receiptMac": mac,
        "ticketId": ticket_id, "claimId": claim.claim_id,
        "ticketBoundInMac": False, "candidateOnly": True,
        "readerProcessIsolated": False,
        "readyAuthorized": False, "releaseAllowed": False,
        "retryAllowed": False}


__all__ = ["BoundedRunReader", "ClaimedProof", "KeyProvider",
    "NotConfigured", "SignerBlocked", "sign_once"]

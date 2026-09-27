"""Default-closed synthetic v11 ticket/claim-bound signing candidate.

The process validates existing staged HTML/XLSX bytes with the owning v11
preflight. It also reads four private Agent ledgers through 0076's per-claim
bounded SQL, then signs one new-purpose canonical receipt. Its test-only
reader still uses the pre-existing ai_writer role; it is not a production
credential boundary or permission to publish renderer 11.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from typing import Protocol

from .contracts import canonical


DOMAIN = b"teruisi:budget-v11:ticket-bound-signer:v3\x00"
SCHEMA = "budget-v11-ticket-bound-signed-receipt-v3"
PURPOSE = "teruisi:business-promotion-budget-v11:ticket-bound-sign-once:v3"
ROLES = frozenset({"commerce","promotion","market_b2b",
    "independent_review","report"})
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
RUN = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
KEY_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
SIGN_ROLE = "teruisi_ai_budget_v11_sign_login"
MAX_SECONDS = 600
READ_0070 = "SELECT public.ai_budget_v11_read_proof_ticket_v2(%s,%s,%s,%s)"
INVENTORY = ("SELECT public.ai_budget_v11_sign_ledger_inventory_v3("
    "%s,%s,%s,%s,%s)")
PAGE = ("SELECT public.ai_budget_v11_read_agent_ledger_v3("
    "%s,%s,%s,%s,%s,%s,%s,%s,%s)")
RECORD = ("SELECT public.ai_budget_v11_record_signed_receipt_v3("
    "%s,%s,%s,%s,%s,%s,%s)")
OUTCOME = "SELECT public.ai_budget_v11_sign_outcome_v3(%s,%s,%s)"


class SignerBlocked(RuntimeError):
    pass


class NotConfigured(SignerBlocked):
    pass


class KeyProvider(Protocol):
    def get_key(self, key_id: str) -> bytes: ...


class _NoKey:
    def get_key(self, key_id: str) -> bytes:
        raise NotConfigured("protected signing key provider is not configured")


DEFAULT_KEY_PROVIDER: KeyProvider = _NoKey()


def _need(value, reason):
    if not value:
        raise SignerBlocked(reason)


def _digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _fixed_unknown(phase: str) -> dict:
    return {"status":"unknown_ticket_bound_signing", "phase":phase,
        "retryAllowed":False,"candidateOnly":True,"releaseAllowed":False,
        "readyAuthorized":False}


def _identity(db, role: str, port: int):
    with db.cursor() as cursor:
        cursor.execute("SELECT session_user,current_user,"
            "(SELECT rolsuper FROM pg_catalog.pg_roles WHERE "
            "rolname=session_user),current_database(),"
            "COALESCE(inet_server_addr()::text,''),inet_server_port(),"
            "EXTRACT(EPOCH FROM current_setting('statement_timeout')::interval)"
            "*1000")
        row = cursor.fetchone()
    _need(type(row) is tuple and len(row)==7 and
        row[:3] == (role,role,False) and
        row[3] == "test_teruisi_ai_rehearsal" and
        row[4] in ("127.0.0.1","127.0.0.1/32","::1","::1/128") and
        row[5] == port and 0 < row[6] <= 600000,
        "isolated signer identity or SQL timeout drift")


def _reader_identity(port: int):
    from django.conf import settings
    from django.db import connection
    _need(settings.DJANGO_PROCESS_ROLE == "ai_writer" and
        settings.DJANGO_EXPECT_READ_ONLY is False and
        not connection.in_atomic_block,
        "synthetic full-byte reader identity drift")
    with connection.cursor() as cursor:
        cursor.execute("SELECT session_user,current_user,"
            "(SELECT rolsuper FROM pg_catalog.pg_roles WHERE "
            "rolname=session_user),current_database(),"
            "COALESCE(inet_server_addr()::text,''),inet_server_port(),"
            "EXTRACT(EPOCH FROM current_setting('statement_timeout')::interval)"
            "*1000")
        row = cursor.fetchone()
    _need(type(row) is tuple and len(row)==7 and
        row[:6] in tuple(("teruisi_ai_writer","teruisi_ai_writer",False,
        "test_teruisi_ai_rehearsal",address,port) for address in
        ("127.0.0.1","127.0.0.1/32","::1","::1/128")) and
        0 < row[6] <= 600000,
        "synthetic full-byte reader database drift")


def _one(db, statement, params):
    with db.cursor() as cursor:
        cursor.execute(statement, params)
        rows = cursor.fetchmany(2)
    _need(len(rows)==1 and len(rows[0])==1 and type(rows[0][0]) is dict,
        "protected SQL response shape drift")
    return rows[0][0]


def _inventory(db, ticket: str, claim: str, run: str, attempt: int,
               proof_sha: str) -> dict:
    value = _one(db, INVENTORY,[ticket,claim,run,attempt,proof_sha])
    _need(type(value) is dict and set(value)=={
        "schemaVersion","ticketId","claimId","runId","attempt",
        "ledgerRoot","jobs","readyAuthorized"} and
        value["schemaVersion"] == "budget-v11-ledger-inventory-v3" and
        value["ticketId"] == ticket and value["claimId"] == claim and
        value["runId"] == run and value["attempt"] == attempt and
        value["readyAuthorized"] is False and
        type(value["ledgerRoot"]) is str and HEX64.fullmatch(value["ledgerRoot"])
        is not None and type(value["jobs"]) is list and len(value["jobs"])==5,
        "protected ledger inventory changed")
    roles=set()
    for item in value["jobs"]:
        _need(type(item) is dict and set(item)=={
            "jobId","role","providerCount","toolCount"} and
            type(item["jobId"]) is str and RUN.fullmatch(item["jobId"]) and
            item["role"] in ROLES and item["role"] not in roles and
            type(item["providerCount"]) is int and
            1 <= item["providerCount"] <= 20 and
            type(item["toolCount"]) is int and
            1 <= item["toolCount"] <= 40,
            "protected ledger job inventory changed")
        roles.add(item["role"])
    _need(roles == ROLES, "protected ledger roles changed")
    return value


def _pages(db, inventory: dict, ticket: str, claim: str,
           run: str, attempt: int, proof_sha: str, deadline: float) -> None:
    root=inventory["ledgerRoot"]
    chain=[]
    for job in inventory["jobs"]:
        for kind,count in (("provider",job["providerCount"]),
                           ("tool",job["toolCount"])):
            for ordinal in range(1,count+1):
                _need(time.monotonic()<=deadline,
                    "ticket-bound ledger page time limit exceeded")
                value=_one(db,PAGE,[ticket,claim,run,attempt,proof_sha,
                    job["jobId"],kind,ordinal,root])
                _need(type(value) is dict and set(value)=={
                    "schemaVersion","ticketId","claimId","runId","attempt",
                    "jobId","kind","ordinal","ledgerRoot","page",
                    "pageSha256","readyAuthorized"} and
                    value["schemaVersion"]=="budget-v11-ledger-page-v3" and
                    (value["ticketId"],value["claimId"],value["runId"],
                     value["attempt"],value["jobId"],value["kind"],
                     value["ordinal"],value["ledgerRoot"],
                     value["readyAuthorized"]) ==
                    (ticket,claim,run,attempt,job["jobId"],kind,ordinal,
                     root,False) and
                    type(value["page"]) is dict and
                    value["pageSha256"] == _digest(value["page"]),
                    "protected ledger page changed")
                chain.append({"jobId":job["jobId"],"kind":kind,
                    "ordinal":ordinal,"pageSha256":value["pageSha256"]})
    _need(_digest({"schemaVersion":"budget-v11-ledger-root-v3",
        "runId":run,"attempt":attempt,"pages":chain})==root,
        "protected ledger page chain differs from SQL root")


def _full_byte(root: dict, run: str, attempt: int, proof_sha: str,
               principal, port: int, deadline: float) -> tuple[str,str]:
    _reader_identity(port)
    from ai_assistant import business_promotion_budget_v11_preflight as preflight
    def checkpoint(*_args, **_kwargs):
        _need(time.monotonic()<=deadline,
            "ticket-bound full-byte time limit exceeded")
    checkpoint()
    fresh=preflight.prepare(run,principal,enabled=True,checkpoint=checkpoint)
    checkpoint()
    _need(type(fresh) is dict and fresh.get("runId")==run and
        fresh.get("attempt")==attempt and
        fresh.get("attestationSha256")==proof_sha and
        fresh.get("attestationText")==root["attestationText"] and
        fresh.get("runVersion")==root["runVersion"] and
        fresh.get("bindingDigest")==root["bindingDigest"] and
        fresh.get("candidateOnly") is True and
        fresh.get("readyAuthorized") is False,
        "fresh HTML/XLSX owning bytes changed")
    assertion=json.loads(fresh["attestationText"])
    _need(canonical(assertion)==fresh["attestationText"],
        "fresh attestation is not canonical")
    file_root=_digest({"schemaVersion":"budget-v11-file-page-root-v3",
        "files":assertion["files"],
        "fileByteVerificationDigest":assertion["fileByteVerificationDigest"],
        "htmlRowsDigest":assertion["htmlRowsDigest"],
        "xlsxOpcFormulaDigest":assertion["xlsxOpcFormulaDigest"]})
    _need(file_root==root["filePageRoot"],
        "fresh HTML/XLSX page root changed")
    return fresh["attestationText"],file_root


def sign_once(sign_db, principal, *, ticket_id: str, run_id: str,
              attempt: int, attestation_sha256: str, key_id: str,
              port: int, key_provider: KeyProvider = DEFAULT_KEY_PROVIDER,
              isolated_candidate: bool = False) -> dict:
    _need(isolated_candidate is True and
        os.getenv("TERUISI_DJANGO_ENVIRONMENT")=="test" and
        type(port) is int and 55440 <= port <= 55999,
        "ticket-bound signing is isolated-test only")
    _need(type(ticket_id) is str and UUID.fullmatch(ticket_id) and
        type(run_id) is str and RUN.fullmatch(run_id) and
        type(attempt) is int and 1 <= attempt <= 5 and
        type(attestation_sha256) is str and HEX64.fullmatch(attestation_sha256)
        and type(key_id) is str and KEY_ID.fullmatch(key_id) and
        getattr(sign_db,"autocommit",None) is True,
        "ticket-bound signer input shape drift")
    key=key_provider.get_key(key_id)
    _need(type(key) is bytes and 32 <= len(key) <= 128,
        "ticket-bound signer key shape drift")
    _identity(sign_db,SIGN_ROLE,port)
    try:
        narrow=_one(sign_db,READ_0070,[ticket_id,run_id,attempt,
            attestation_sha256])
    except Exception:
        return _fixed_unknown("claim_reply")
    claim=narrow.get("claimId")
    if (type(claim) is not str or UUID.fullmatch(claim) is None or
            narrow.get("ticketId")!=ticket_id or narrow.get("runId")!=run_id or
            narrow.get("attempt")!=attempt or
            narrow.get("attestationSha256")!=attestation_sha256 or
            narrow.get("readyAuthorized") is not False):
        return _fixed_unknown("claim_shape")
    phase="inventory"
    deadline=time.monotonic()+MAX_SECONDS
    try:
        inventory=_inventory(sign_db,ticket_id,claim,run_id,attempt,
            attestation_sha256)
        phase="ledger_pages"
        _pages(sign_db,inventory,ticket_id,claim,run_id,attempt,
            attestation_sha256,deadline)
        phase="full_bytes"
        requirements=_one(sign_db,
            "SELECT public.ai_budget_v11_sign_requirements_v3(%s,%s,%s,%s,%s)",
            [ticket_id,claim,run_id,attempt,attestation_sha256])
        _full_byte(requirements,run_id,attempt,attestation_sha256,
            principal,port,deadline)
        phase="final_inventory"
        _need(time.monotonic()<=deadline,
            "ticket-bound signing time limit exceeded")
        final=_inventory(sign_db,ticket_id,claim,run_id,attempt,
            attestation_sha256)
        _need(final==inventory,"protected ledger changed during full-byte read")
        body={"schemaVersion":SCHEMA,"purpose":PURPOSE,"keyId":key_id,
            "ticketId":ticket_id,"claimId":claim,"runId":run_id,
            "attempt":attempt,"runVersion":requirements["runVersion"],
            "workflowVersion":requirements["workflowVersion"],
            "reportId":requirements["reportId"],
            "ownerEmail":requirements["ownerEmail"],
            "bindingDigest":requirements["bindingDigest"],
            "oldAttestationId":requirements["oldAttestationId"],
            "loginAttestationId":requirements["loginAttestationId"],
            "attestationSha256":attestation_sha256,
            "ledgerRoot":inventory["ledgerRoot"],
            "filePageRoot":requirements["filePageRoot"]}
        raw=canonical(body)
        _need(len(raw.encode("utf-8"))<=262144,
            "ticket-bound receipt exceeds limit")
        mac=hmac.new(key,DOMAIN+raw.encode("utf-8"),hashlib.sha256).hexdigest()
        receipt_sha=hashlib.sha256(raw.encode("utf-8")).hexdigest()
        phase="record_reply"
        with sign_db.cursor() as cursor:
            cursor.execute(RECORD,[ticket_id,claim,run_id,attempt,
                attestation_sha256,raw,mac])
            rows=cursor.fetchmany(2)
        _need(len(rows)==1 and len(rows[0])==1 and rows[0][0] is not None,
            "ticket-bound record reply changed")
    except Exception:
        value={**_fixed_unknown(phase),"ticketId":ticket_id,
            "claimId":claim}
        if phase=="record_reply":
            # The exact canonical body was signed before the uncertain SQL
            # result. Outcome may be read later; RECORD must never replay.
            value["receiptSha256"]=receipt_sha
        return value
    return {"status":"signed_candidate_unpublished",
        "ticketId":ticket_id,"claimId":claim,"receiptId":str(rows[0][0]),
        "receiptSha256":receipt_sha,"ledgerRoot":inventory["ledgerRoot"],
        "filePageRoot":requirements["filePageRoot"],
        "ticketBoundInMac":True,"readerProcessIsolated":False,
        "candidateOnly":True,"readyAuthorized":False,
        "releaseAllowed":False,"retryAllowed":False}


def outcome(sign_db, *, ticket_id: str, claim_id: str,
            receipt_sha256: str, port: int) -> dict:
    _need(type(ticket_id) is str and UUID.fullmatch(ticket_id) and
        type(claim_id) is str and UUID.fullmatch(claim_id) and
        type(receipt_sha256) is str and HEX64.fullmatch(receipt_sha256),
        "ticket-bound outcome request invalid")
    _identity(sign_db,SIGN_ROLE,port)
    value=_one(sign_db,OUTCOME,[ticket_id,claim_id,receipt_sha256])
    _need(type(value) is dict and value.get("schemaVersion")==
        "budget-v11-sign-outcome-v3" and value.get("status") in
        {"committed","conflict","absent_observed"} and
        value.get("retryAllowed") is False,
        "ticket-bound outcome shape drift")
    return {**value,"releaseAllowed":False,"readyAuthorized":False}


__all__=["KeyProvider","NotConfigured","SignerBlocked","sign_once","outcome"]

"""Pure fixed-purpose 0078 request hash; never grants ready or download."""
from __future__ import annotations

import hashlib
import re

from .contracts import canonical


SCHEMA="budget-v11-signed-publication-request-v2"
HEX64=re.compile(r"[0-9a-f]{64}\Z")
RUN=re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
KEY=re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
UUID=re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


class PublicationBlocked(ValueError):
    pass


def request(*, run_id:str,attempt:int,expected_version:int,
            signed_receipt_id:str,signed_receipt_sha256:str,
            ticket_id:str,claim_id:str,report_id:str,owner_email:str,
            binding_digest:str,ledger_root:str,file_page_root:str,
            key_id:str)->dict:
    if (type(run_id) is not str or RUN.fullmatch(run_id) is None or
            type(attempt) is not int or not 1<=attempt<=5 or
            type(expected_version) is not int or expected_version<1 or
            any(type(value) is not str or UUID.fullmatch(value) is None
                for value in (signed_receipt_id,ticket_id,claim_id)) or
            any(type(value) is not str or HEX64.fullmatch(value) is None
                for value in (signed_receipt_sha256,binding_digest,
                    ledger_root,file_page_root)) or
            type(report_id) is not str or not 1<=len(report_id)<=160 or
            type(owner_email) is not str or not 3<=len(owner_email)<=320 or
            owner_email!=owner_email.lower() or
            type(key_id) is not str or KEY.fullmatch(key_id) is None):
        raise PublicationBlocked("0078 publication request shape invalid")
    body={"schemaVersion":SCHEMA,"runId":run_id,"attempt":attempt,
        "expectedVersion":expected_version,"signedReceiptId":signed_receipt_id,
        "signedReceiptSha256":signed_receipt_sha256,
        "ticketId":ticket_id,"claimId":claim_id,"reportId":report_id,
        "ownerEmail":owner_email,"bindingDigest":binding_digest,
        "ledgerRoot":ledger_root,"filePageRoot":file_page_root,
        "keyId":key_id}
    raw=canonical(body)
    return {"request":body,"requestJson":raw,
        "requestDigest":hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "candidateOnly":True,"readyAuthorized":False,
        "releaseAllowed":False}


def outcome(value:object,*,request_digest:str)->dict:
    status=value.get("status") if type(value) is dict else None
    common={"schemaVersion","status","publicationId","retryAllowed",
        "readyAuthorized"}
    expected=common|({"requestDigest"} if status=="committed" else set())
    if (type(value) is not dict or value.get("schemaVersion")!=
            "budget-v11-signed-publication-outcome-v2" or
            status not in
            {"committed","conflict","absent_observed"} or
            set(value)!=expected or
            value.get("retryAllowed") is not False or
            value.get("readyAuthorized") is not False or
            (status=="committed" and
             (value.get("requestDigest")!=request_digest or
              type(value.get("publicationId")) is not str or
              UUID.fullmatch(value["publicationId"]) is None)) or
            (status!="committed" and value.get("publicationId") is not None)):
        raise PublicationBlocked("0078 publication outcome changed")
    return {**value,"candidateOnly":True,"releaseAllowed":False}

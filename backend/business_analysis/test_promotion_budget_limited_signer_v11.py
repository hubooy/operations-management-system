"""Pure, default-closed checks for the isolated 0067/0070 signer bridge."""
import hashlib
import json
import os
import types
import unittest
from unittest.mock import patch

from .contracts import canonical
from . import promotion_budget_limited_signer_v11 as limited


ZERO = "0" * 64
TICKET = "12345678-1234-1234-1234-123456789abc"
CLAIM = "12345678-1234-1234-1234-123456789abd"


def proof():
    body = {"schemaVersion":"business-promotion-budget-v11-staged-attestation-v1",
        "runId":"run1", "attempt":1, "runVersion":3,
        "bindingDigest":ZERO, "files":{"files":[],"manifestFile":{}},
        "compactJsonSha256":ZERO, "fullManifestSha256":ZERO,
        "fullManifestDigest":ZERO, "approvedContentDigest":ZERO,
        "humanReviewDigest":ZERO, "budgetProofDigest":ZERO,
        "slimProofDigest":ZERO, "fileByteVerificationDigest":ZERO,
        "htmlRowsDigest":ZERO, "xlsxOpcFormulaDigest":ZERO,
        "owningVerificationDigest":ZERO, "reportSnapshotSha256":ZERO,
        "workflowInputSha256":ZERO}
    raw = canonical(body)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def result():
    raw, sha = proof()
    return {"schemaVersion":limited.SCHEMA, "ticketId":TICKET,
        "claimId":CLAIM, "runId":"run1", "attempt":1,"runVersion":3,
        "reportId":"report1", "ownerEmail":"owner@example.test",
        "bindingDigest":ZERO, "attestationId":ZERO,
        "attestationSha256":sha,"attestationText":raw,
        "readyAuthorized":False}


def fresh():
    raw, sha = proof()
    return {"schemaVersion":"business-promotion-budget-v11-owning-preflight-v1",
        "runId":"run1", "attempt":1, "runVersion":3,
        "bindingDigest":ZERO,"attestationText":raw,
        "attestationSha256":sha,"candidateOnly":True,
        "readyAuthorized":False,
        "databaseCanIndependentlyVerifyProcessAssertions":False}


class Cursor:
    def __init__(self, db):
        self.db = db
        self.query = ""
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, query, params=None):
        self.query = query
        if limited.READ_SQL == query:
            self.db.claims += 1
            self.db.params = params
            if self.db.fail_reply:
                raise ConnectionError("synthetic lost reply")
    def fetchone(self):
        return (limited.SIGN_ROLE, limited.SIGN_ROLE, False,
            "test_teruisi_ai_rehearsal", "127.0.0.1", 55786)
    def fetchmany(self, n): return [(self.db.payload,)]


class Db:
    autocommit = True
    fail_reply = False
    def __init__(self, payload):
        self.payload = payload
        self.claims = 0
        self.params = None
    def cursor(self): return Cursor(self)


class SyntheticKey:
    def get_key(self, key_id):
        if key_id != "synthetic-only":
            raise AssertionError("wrong key identity")
        return b"x" * 32


class LimitedSignerTests(unittest.TestCase):
    def request(self, db, reader, **overrides):
        args = {"ticket_id":TICKET,"run_id":"run1","attempt":1,
            "attestation_sha256":proof()[1],"key_id":"synthetic-only",
            "key_provider":SyntheticKey(),"isolated_candidate":True}
        args.update(overrides)
        with patch.dict(os.environ, {"TERUISI_DJANGO_ENVIRONMENT":"test"}):
            return limited.sign_once(db, reader, **args)

    def test_default_refuses_before_key_or_ticket_claim(self):
        db = Db(result())
        reader = limited.BoundedRunReader("owner", expected_port=55786)
        with self.assertRaises(limited.SignerBlocked):
            self.request(db, reader, isolated_candidate=False)
        self.assertEqual(db.claims, 0)
        with patch.dict(os.environ, {"TERUISI_DJANGO_ENVIRONMENT":"test"}):
            with self.assertRaises(limited.NotConfigured):
                limited.sign_once(db, reader, ticket_id=TICKET,
                    run_id="run1", attempt=1, attestation_sha256=proof()[1],
                    key_id="synthetic-only", isolated_candidate=True)
        self.assertEqual(db.claims, 0)

    def test_isolated_identity_accepts_only_exact_role_db_port_and_loopback(self):
        base = (limited.SIGN_ROLE,limited.SIGN_ROLE,False,
            "test_teruisi_ai_rehearsal","127.0.0.1/32",55786)
        limited._isolated_identity(base,limited.SIGN_ROLE,55786)
        for position,value in ((0,"teruisi_ai_writer"),(1,"teruisi_ai_writer"),
                (2,True),(3,"teruisi_sales"),(4,"10.0.0.1"),(5,5432)):
            changed=list(base); changed[position]=value
            with self.subTest(position=position), self.assertRaises(
                    limited.SignerBlocked):
                limited._isolated_identity(tuple(changed),limited.SIGN_ROLE,55786)

    def test_isolated_signing_uses_one_claim_and_never_authorizes_release(self):
        db = Db(result())
        reader = limited.BoundedRunReader("owner", expected_port=55786)
        with patch.object(limited.BoundedRunReader, "rebuild", return_value=fresh()) as rebuild:
            signed = self.request(db, reader)
        self.assertEqual(db.claims, 1)
        self.assertEqual(db.params, [TICKET,"run1",1,proof()[1]])
        rebuild.assert_called_once()
        self.assertEqual(signed["status"], "signed_candidate_unpublished")
        self.assertEqual((signed["ticketBoundInMac"],signed["readyAuthorized"],
            signed["releaseAllowed"],signed["retryAllowed"],
            signed["readerProcessIsolated"]),
            (False,False,False,False,False))
        self.assertEqual(len(signed["receiptMac"]),64)
        self.assertNotIn("ticketId", json.loads(signed["receiptText"]))

    def test_lost_reply_or_changed_owned_bytes_never_reclaims(self):
        db = Db(result())
        db.fail_reply = True
        reader = limited.BoundedRunReader("owner", expected_port=55786)
        self.assertEqual(self.request(db,reader)["status"],"unknown_ticket_claim")
        self.assertEqual(db.claims,1)
        db = Db(result())
        with patch.object(limited.BoundedRunReader,"rebuild",
                side_effect=limited.SignerBlocked("file drift")):
            value = self.request(db,reader)
        self.assertEqual(value["status"],"unknown_after_ticket_claim")
        self.assertFalse(value["retryAllowed"])
        self.assertEqual(db.claims,1)
        self.assertEqual(value["reasonCode"],"owning_unknown_failed")

    def test_fixed_failure_stage_never_reflects_exception_text(self):
        reader=limited.BoundedRunReader("owner",expected_port=55786)
        reader._stage="full_bytes"
        secret="postgresql://role:synthetic-secret@127.0.0.1/db"
        self.assertEqual(limited._after_claim_reason(RuntimeError(secret),
            "reader",reader),"owning_full_bytes_failed")
        self.assertEqual(limited._after_claim_reason(RuntimeError(secret),
            "receipt_mac",reader),"hmac_candidate_failed")
        privilege=RuntimeError(secret)
        privilege.sqlstate="42501"
        self.assertEqual(limited._after_claim_reason(privilege,"reader",reader),
            "owning_reader_acl_denied")

    def test_closed_reader_acl_consumes_only_one_ticket_without_mac(self):
        db=Db(result())
        reader=limited.BoundedRunReader("owner",expected_port=55786)
        denied=RuntimeError("sensitive database diagnostic is not returned")
        denied.sqlstate="42501"
        with patch.object(limited.BoundedRunReader,"rebuild",
                side_effect=denied):
            value=self.request(db,reader)
        self.assertEqual(db.claims,1)
        self.assertEqual(value["status"],"unknown_after_ticket_claim")
        self.assertEqual(value["reasonCode"],"owning_reader_acl_denied")
        self.assertEqual((value["releaseAllowed"],value["retryAllowed"],
            value["ticketBoundInMac"]),(False,False,False))
        self.assertNotIn("receiptText",value)
        self.assertNotIn("receiptMac",value)

    def test_claim_identity_rejects_cross_run_owner_version_and_digest(self):
        for field,value in (("runId","run2"),("attempt",2),
                ("ownerEmail",""),("runVersion",4),
                ("attestationSha256","1"*64),
                ("bindingDigest","x"*64),
                ("attestationText",proof()[0]+" ")):
            with self.subTest(field=field):
                changed=result(); changed[field]=value
                with self.assertRaises(limited.SignerBlocked):
                    limited.ClaimedProof.from_result(changed,ticket_id=TICKET,
                        run_id="run1",attempt=1,
                        attestation_sha256=proof()[1])

    def test_bounded_reader_rechecks_row_and_exact_full_byte_result(self):
        claim = limited.ClaimedProof.from_result(result(), ticket_id=TICKET,
            run_id="run1",attempt=1,attestation_sha256=proof()[1])
        reader = limited.BoundedRunReader("owner",expected_port=55786)
        row = types.SimpleNamespace(id="run1",attempt=1,version=3,
            report_id="report1",owner_email="owner@example.test",
            binding_digest=ZERO,renderer_version=11,status="paused",
            error_code="renderer_unpublished",draft=False)
        identity = ("teruisi_ai_reader","teruisi_ai_reader",False,
            "test_teruisi_ai_rehearsal","127.0.0.1",55786)
        with (patch.object(reader,"_identity",return_value=identity),
              patch.object(reader,"_load_run",side_effect=[row,row]) as load,
              patch.object(reader,"_prepare_full",return_value=fresh()) as prepare):
            self.assertEqual(reader.rebuild(claim),fresh())
        self.assertEqual(load.call_count,2)
        prepare.assert_called_once()
        altered=types.SimpleNamespace(**{**vars(row),"version":4})
        with (patch.object(reader,"_identity",return_value=identity),
              patch.object(reader,"_load_run",side_effect=[row,altered]),
              patch.object(reader,"_prepare_full",return_value=fresh())):
            with self.assertRaises(limited.SignerBlocked):
                reader.rebuild(claim)
        with (patch.object(reader,"_identity",return_value=identity),
              patch.object(reader,"_load_run",side_effect=[row,row]),
              patch.object(reader,"_prepare_full",return_value={**fresh(),
                  "attestationSha256":"f"*64})):
            with self.assertRaises(limited.SignerBlocked):
                reader.rebuild(claim)


if __name__ == "__main__":
    unittest.main()

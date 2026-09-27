"""Pure contract tests; only synthetic key material, no production signing."""
import hashlib
import hmac
import json
import os
import time
import unittest
from unittest.mock import patch

from . import promotion_budget_ticket_signer_v11 as signer


TICKET="12345678-1234-1234-1234-123456789abc"
CLAIM="12345678-1234-1234-1234-123456789abd"
RECEIPT="12345678-1234-1234-1234-123456789abe"
SHA="a"*64
ROOT="b"*64
FILE="c"*64


class Key:
    def get_key(self,key_id):
        if key_id!="synthetic": raise AssertionError("wrong key")
        return b"k"*32


class Cursor:
    def __init__(self,db): self.db=db
    def __enter__(self): return self
    def __exit__(self,*_): return False
    def execute(self,query,params):
        self.db.record=(query,params)
        if self.db.fail_record: raise ConnectionError("synthetic lost reply")
    def fetchmany(self,n): return [(RECEIPT,)]


class Db:
    autocommit=True
    fail_record=False
    record=None
    def cursor(self): return Cursor(self)


def narrow():
    return {"ticketId":TICKET,"claimId":CLAIM,"runId":"run1",
        "attempt":1,"attestationSha256":SHA,"readyAuthorized":False}


def inventory():
    return {"schemaVersion":"budget-v11-ledger-inventory-v3",
        "ticketId":TICKET,"claimId":CLAIM,"runId":"run1",
        "attempt":1,"ledgerRoot":ROOT,"readyAuthorized":False,
        "jobs":[{"jobId":"job-"+role,"role":role,
            "providerCount":1,"toolCount":1} for role in sorted(signer.ROLES)]}


def requirements():
    return {"runVersion":3,"workflowVersion":7,"reportId":"report1",
        "ownerEmail":"owner@example.test","bindingDigest":"d"*64,
        "oldAttestationId":"e"*64,"loginAttestationId":"f"*64,
        "filePageRoot":FILE,"attestationText":"{}"}


class TicketSignerPureTests(unittest.TestCase):
    def kwargs(self,**changed):
        value={"ticket_id":TICKET,"run_id":"run1","attempt":1,
            "attestation_sha256":SHA,"key_id":"synthetic","port":55787,
            "key_provider":Key(),"isolated_candidate":True}
        value.update(changed)
        return value

    def test_default_and_missing_key_refuse_before_claim(self):
        db=Db()
        with self.assertRaises(signer.SignerBlocked):
            signer.sign_once(db,object(),**self.kwargs(isolated_candidate=False))
        with patch.dict(os.environ,{"TERUISI_DJANGO_ENVIRONMENT":"test"}):
            with self.assertRaises(signer.NotConfigured):
                signer.sign_once(db,object(),**self.kwargs(
                    key_provider=signer.DEFAULT_KEY_PROVIDER))
        self.assertIsNone(db.record)

    def test_receipt_mac_covers_claim_ledger_and_file_roots(self):
        db=Db()
        def answer(_db,statement,_params):
            if statement==signer.READ_0070:return narrow()
            if statement==signer.INVENTORY:return inventory()
            return requirements()
        with (patch.dict(os.environ,{"TERUISI_DJANGO_ENVIRONMENT":"test"}),
              patch.object(signer,"_identity"),
              patch.object(signer,"_one",side_effect=answer),
              patch.object(signer,"_pages") as pages,
              patch.object(signer,"_full_byte") as full):
            value=signer.sign_once(db,object(),**self.kwargs())
        self.assertEqual(value["status"],"signed_candidate_unpublished")
        self.assertEqual((value["ticketBoundInMac"],value["releaseAllowed"],
            value["readerProcessIsolated"]),(True,False,False))
        pages.assert_called_once()
        full.assert_called_once()
        self.assertEqual(db.record[0],signer.RECORD)
        raw,mac=db.record[1][-2:]
        body=json.loads(raw)
        self.assertEqual((body["ticketId"],body["claimId"],
            body["ledgerRoot"],body["filePageRoot"]),(TICKET,CLAIM,ROOT,FILE))
        self.assertEqual(body["workflowVersion"],7)
        self.assertEqual(mac,hmac.new(b"k"*32,
            signer.DOMAIN+raw.encode(),hashlib.sha256).hexdigest())

    def test_claim_or_record_uncertainty_never_replays(self):
        db=Db()
        with (patch.dict(os.environ,{"TERUISI_DJANGO_ENVIRONMENT":"test"}),
              patch.object(signer,"_identity"),
              patch.object(signer,"_one",side_effect=ConnectionError("lost"))):
            value=signer.sign_once(db,object(),**self.kwargs())
        self.assertEqual(value["phase"],"claim_reply")
        self.assertFalse(value["retryAllowed"])
        self.assertIsNone(db.record)
        db=Db();db.fail_record=True
        def answer(_db,statement,_params):
            if statement==signer.READ_0070:return narrow()
            if statement==signer.INVENTORY:return inventory()
            return requirements()
        with (patch.dict(os.environ,{"TERUISI_DJANGO_ENVIRONMENT":"test"}),
              patch.object(signer,"_identity"),
              patch.object(signer,"_one",side_effect=answer),
              patch.object(signer,"_pages"),
              patch.object(signer,"_full_byte")):
            value=signer.sign_once(db,object(),**self.kwargs())
        self.assertEqual(value["phase"],"record_reply")
        self.assertFalse(value["retryAllowed"])
        self.assertEqual(db.record[0],signer.RECORD)
        self.assertEqual(value["receiptSha256"],hashlib.sha256(
            db.record[1][-2].encode()).hexdigest())
        self.assertEqual(value["claimId"],CLAIM)

    def test_five_roles_and_page_digest_are_exact(self):
        value=inventory()
        with patch.object(signer,"_one",return_value=value):
            self.assertEqual(signer._inventory(None,TICKET,CLAIM,"run1",1,SHA),value)
        bad=inventory();bad["jobs"][0]["role"]="other"
        with patch.object(signer,"_one",return_value=bad):
            with self.assertRaises(signer.SignerBlocked):
                signer._inventory(None,TICKET,CLAIM,"run1",1,SHA)
        for count_field,bad_count in (("providerCount",0),("providerCount",21),
                ("toolCount",0),("toolCount",41)):
            bad=inventory();bad["jobs"][0][count_field]=bad_count
            with patch.object(signer,"_one",return_value=bad):
                with self.assertRaises(signer.SignerBlocked):
                    signer._inventory(None,TICKET,CLAIM,"run1",1,SHA)

    def test_page_chain_recomputes_sql_root_and_rejects_tamper(self):
        jobs=inventory()["jobs"]
        chain=[];responses=[]
        for job in jobs:
            for kind in ("provider","tool"):
                page={"jobId":job["jobId"],"kind":kind,"ordinal":1}
                page_sha=signer._digest(page)
                chain.append({"jobId":job["jobId"],"kind":kind,
                    "ordinal":1,"pageSha256":page_sha})
                responses.append({"schemaVersion":"budget-v11-ledger-page-v3",
                    "ticketId":TICKET,"claimId":CLAIM,"runId":"run1",
                    "attempt":1,"jobId":job["jobId"],"kind":kind,
                    "ordinal":1,"ledgerRoot":"", "page":page,
                    "pageSha256":page_sha,"readyAuthorized":False})
        root=signer._digest({"schemaVersion":"budget-v11-ledger-root-v3",
            "runId":"run1","attempt":1,"pages":chain})
        for item in responses:item["ledgerRoot"]=root
        actual=inventory();actual["ledgerRoot"]=root
        with patch.object(signer,"_one",side_effect=responses):
            signer._pages(None,actual,TICKET,CLAIM,"run1",1,SHA,
                time.monotonic()+10)
        tampered=[dict(item) for item in responses]
        tampered[3]["pageSha256"]="0"*64
        with patch.object(signer,"_one",side_effect=tampered):
            with self.assertRaises(signer.SignerBlocked):
                signer._pages(None,actual,TICKET,CLAIM,"run1",1,SHA,
                    time.monotonic()+10)
        wrong=inventory();wrong["ledgerRoot"]="0"*64
        with patch.object(signer,"_one",side_effect=responses):
            with self.assertRaises(signer.SignerBlocked):
                signer._pages(None,wrong,TICKET,CLAIM,"run1",1,SHA,
                    time.monotonic()+10)

    def test_pure_maximum_300_page_chain_is_bounded(self):
        value=inventory()
        for job in value["jobs"]:
            job["providerCount"]=20
            job["toolCount"]=40
        chain=[];responses=[]
        for job in value["jobs"]:
            for kind,count in (("provider",20),("tool",40)):
                for ordinal in range(1,count+1):
                    page={"jobId":job["jobId"],"kind":kind,
                        "ordinal":ordinal}
                    page_sha=signer._digest(page)
                    chain.append({"jobId":job["jobId"],"kind":kind,
                        "ordinal":ordinal,"pageSha256":page_sha})
                    responses.append({"schemaVersion":"budget-v11-ledger-page-v3",
                        "ticketId":TICKET,"claimId":CLAIM,"runId":"run1",
                        "attempt":1,"jobId":job["jobId"],"kind":kind,
                        "ordinal":ordinal,"ledgerRoot":"", "page":page,
                        "pageSha256":page_sha,"readyAuthorized":False})
        self.assertEqual(len(chain),300)
        root=signer._digest({"schemaVersion":"budget-v11-ledger-root-v3",
            "runId":"run1","attempt":1,"pages":chain})
        value["ledgerRoot"]=root
        for item in responses:item["ledgerRoot"]=root
        with patch.object(signer,"_one",side_effect=responses) as fetched:
            signer._pages(None,value,TICKET,CLAIM,"run1",1,SHA,
                time.monotonic()+10)
        self.assertEqual(fetched.call_count,300)


if __name__=="__main__":
    unittest.main()

"""No database: 0078 request and unknown-result boundaries."""
import unittest

from . import promotion_budget_v11_publication_request as p


UUID1="12345678-1234-1234-1234-123456789abc"
UUID2="12345678-1234-1234-1234-123456789abd"
UUID3="12345678-1234-1234-1234-123456789abe"


def valid():
    return dict(run_id="run1",attempt=1,expected_version=3,
        signed_receipt_id=UUID1,signed_receipt_sha256="a"*64,
        ticket_id=UUID2,claim_id=UUID3,report_id="report1",
        owner_email="owner@example.test",binding_digest="b"*64,
        ledger_root="c"*64,file_page_root="d"*64,key_id="synthetic")


class PublicationRequestTests(unittest.TestCase):
    def test_request_has_exact_current_roots_and_never_authorizes_ready(self):
        built=p.request(**valid())
        self.assertEqual(len(built["requestDigest"]),64)
        self.assertFalse(built["readyAuthorized"])
        self.assertFalse(built["releaseAllowed"])
        for field,changed in (("run_id","run2"),("attempt",2),
                ("expected_version",4),("claim_id",UUID1),
                ("ledger_root","e"*64),("file_page_root","f"*64)):
            with self.subTest(field=field):
                other={**valid(),field:changed}
                self.assertNotEqual(p.request(**other)["requestDigest"],
                    built["requestDigest"])

    def test_bad_identity_and_unrelated_outcome_fail_closed(self):
        for field,changed in (("run_id","../x"),("attempt",True),
                ("expected_version",0),("owner_email","OWNER@example.test"),
                ("ticket_id","bad"),("key_id","")):
            with self.subTest(field=field),self.assertRaises(p.PublicationBlocked):
                p.request(**{**valid(),field:changed})
        digest=p.request(**valid())["requestDigest"]
        good={"schemaVersion":"budget-v11-signed-publication-outcome-v2",
            "status":"committed","publicationId":UUID1,
            "requestDigest":digest,"retryAllowed":False,
            "readyAuthorized":False}
        self.assertFalse(p.outcome(good,request_digest=digest)["releaseAllowed"])
        with self.assertRaises(p.PublicationBlocked):
            p.outcome({**good,"requestDigest":"0"*64},request_digest=digest)
        with self.assertRaises(p.PublicationBlocked):
            p.outcome({**good,"retryAllowed":True},request_digest=digest)
        for changed in ({**good,"unexpected":"accepted"},
                {**good,"publicationId":None},
                {**good,"status":"absent_observed","requestDigest":digest},
                {"schemaVersion":good["schemaVersion"],
                    "status":"conflict","publicationId":UUID2,
                    "retryAllowed":False,"readyAuthorized":False}):
            with self.subTest(changed=changed),self.assertRaises(
                    p.PublicationBlocked):
                p.outcome(changed,request_digest=digest)


if __name__=="__main__": unittest.main()

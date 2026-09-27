"""Known-purpose test vectors and fail-closed v4 segment MAC checks."""
import hashlib
import hmac
from unittest import TestCase

from .contracts import AnalysisContractError
from .v4_sealer_mac import (SEGMENT_PURPOSE, derive_segment_key,
                            segment_key_id, verify_segment)


class V4SealerMacTests(TestCase):
    def test_known_segment_purpose_matches_0036_bytes(self):
        master = "test-only-secret-material-0123456789abcdef"
        key = derive_segment_key(master)
        self.assertEqual(key, hmac.new(master.encode("utf-8"), SEGMENT_PURPOSE,
                                        hashlib.sha256).digest())
        payload = {"schemaVersion": "business-v4-validation-attempt-candidate-v1",
                   "segmentIndex": 1, "runId": "test-run"}
        mac = hmac.new(key, ("{\"runId\":\"test-run\",\"schemaVersion\":"
            "\"business-v4-validation-attempt-candidate-v1\",\"segmentIndex\":1}")
            .encode("utf-8"), hashlib.sha256).hexdigest()
        self.assertTrue(verify_segment(payload, mac, key, segment_key_id(key)))
        for wrong_mac, wrong_key_id in (("0" * 64, segment_key_id(key)),
                                        (mac, "0" * 16)):
            with self.assertRaises(AnalysisContractError):
                verify_segment(payload, wrong_mac, key, wrong_key_id)

    def test_no_master_or_wrong_derived_key_is_accepted(self):
        with self.assertRaises(AnalysisContractError):
            derive_segment_key("short")
        with self.assertRaises(AnalysisContractError):
            verify_segment({}, "0" * 64, b"0" * 31, "0" * 16)

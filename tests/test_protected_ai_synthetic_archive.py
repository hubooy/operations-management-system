"""Pure failure-closed checks for the isolated protected AI archive envelope."""

from __future__ import annotations

import hashlib
from pathlib import Path
import secrets
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import protected_ai_synthetic_archive as archive


class SyntheticArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key = secrets.token_bytes(32)
        self.context = hashlib.sha256(b"synthetic role and row inventory").digest()
        self.plaintext = b"PGDMP" + secrets.token_bytes(256)
        self.envelope = archive.encrypt(self.plaintext, self.key, self.context)

    def test_round_trip_has_no_plaintext_prefix(self) -> None:
        self.assertFalse(self.envelope.startswith(b"PGDMP"))
        self.assertEqual(
            archive.decrypt(self.envelope, self.key, self.context),
            self.plaintext)

    def test_wrong_key_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "authentication failed"):
            archive.decrypt(self.envelope, secrets.token_bytes(32), self.context)

    def test_ciphertext_and_tag_tampering_fail(self) -> None:
        for offset in (archive.HEADER_BYTES, len(self.envelope) - 1):
            with self.subTest(offset=offset):
                changed = bytearray(self.envelope)
                changed[offset] ^= 1
                with self.assertRaisesRegex(ValueError, "authentication failed"):
                    archive.decrypt(bytes(changed), self.key, self.context)

    def test_truncation_fails(self) -> None:
        for shorter in (self.envelope[:-1], self.envelope[:archive.HEADER_BYTES + 15]):
            with self.subTest(length=len(shorter)):
                with self.assertRaises(ValueError):
                    archive.decrypt(shorter, self.key, self.context)

    def test_context_or_version_mismatch_fails(self) -> None:
        wrong_context = hashlib.sha256(b"different synthetic source").digest()
        with self.assertRaisesRegex(ValueError, "identity is invalid"):
            archive.decrypt(self.envelope, self.key, wrong_context)
        changed = bytearray(self.envelope)
        changed[len(archive.MAGIC)] ^= 1
        with self.assertRaisesRegex(ValueError, "identity is invalid"):
            archive.decrypt(bytes(changed), self.key, self.context)

    def test_non_custom_input_and_bad_key_fail(self) -> None:
        with self.assertRaises(ValueError):
            archive.encrypt(b"not a dump", self.key, self.context)
        with self.assertRaises(ValueError):
            archive.encrypt(self.plaintext, b"too short", self.context)


if __name__ == "__main__":
    unittest.main()

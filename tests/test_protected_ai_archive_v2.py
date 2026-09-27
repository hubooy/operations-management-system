"""Pure, synthetic checks for the unused protected archive v2 format."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import secrets
import struct
import sys
import tempfile
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import protected_ai_archive_v2 as archive


class _SyntheticKeys:
    def __init__(self, key: bytes):
        self.key = key
        self.calls: list[tuple[str, str]] = []

    def resolve_key(self, key_id: str, purpose: str) -> bytes:
        self.calls.append((key_id, purpose))
        return self.key


class ProtectedArchiveV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.key = secrets.token_bytes(32)
        self.key_id = "synthetic_key_1"
        self.context = hashlib.sha256(b"synthetic owner and ACL evidence").hexdigest()
        self.plaintext = b"PGDMP" + secrets.token_bytes(
            archive.CHUNK_BYTES * 2 + 19)
        self.provider = _SyntheticKeys(self.key)
        self.envelope = archive.seal_archive(self.plaintext,
            key_id=self.key_id, context_sha256=self.context,
            key_provider=self.provider)

    def _open(self, data: bytes, provider: _SyntheticKeys | None = None):
        return archive.open_archive(data, expected_key_id=self.key_id,
            expected_context_sha256=self.context,
            key_provider=provider or self.provider)

    def _pieces(self):
        manifest_size = struct.unpack_from(">I", self.envelope,
            len(archive.MAGIC))[0]
        header_end = len(archive.MAGIC) + 4 + manifest_size
        offset = header_end
        pieces = []
        for _ in range(3):
            start = offset
            _, size = struct.unpack_from(">II", self.envelope, offset)
            offset += 8 + size + archive.TAG_BYTES
            pieces.append(self.envelope[start:offset])
        return self.envelope[:header_end], pieces, self.envelope[offset:]

    def test_round_trip_authenticates_manifest_order_size_and_digest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="protected-archive-v2-") as path:
            verified = self._open(self.envelope)
            self.assertEqual(verified.plaintext, self.plaintext)
            self.assertEqual(verified.manifest["version"], 2)
            self.assertEqual(verified.manifest["chunkCount"], 3)
            self.assertEqual(verified.manifest["plaintextBytes"], len(self.plaintext))
            self.assertEqual(verified.manifest["plaintextSha256"],
                hashlib.sha256(self.plaintext).hexdigest())
            self.assertFalse(self.envelope.startswith(b"PGDMP"))
            self.assertEqual(list(Path(path).iterdir()), [])
        self.assertEqual(self.provider.calls, [
            (self.key_id, "seal"), (self.key_id, "open")])

    def test_default_provider_refuses_seal_and_open(self) -> None:
        with self.assertRaises(archive.NotConfigured):
            archive.seal_archive(self.plaintext, key_id=self.key_id,
                context_sha256=self.context)
        with self.assertRaises(archive.NotConfigured):
            archive.open_archive(self.envelope, expected_key_id=self.key_id,
                expected_context_sha256=self.context)

    def test_wrong_key_and_expected_context_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "authentication failed"):
            self._open(self.envelope, _SyntheticKeys(secrets.token_bytes(32)))
        with self.assertRaisesRegex(ValueError, "expected identity"):
            archive.open_archive(self.envelope, expected_key_id=self.key_id,
                expected_context_sha256=hashlib.sha256(b"other").hexdigest(),
                key_provider=self.provider)

    def test_ciphertext_tag_manifest_and_final_frame_tamper_refused(self) -> None:
        header, pieces, tail = self._pieces()
        cases = []
        changed = bytearray(self.envelope)
        changed[len(header) + 8] ^= 1
        cases.append(bytes(changed))
        changed = bytearray(self.envelope)
        changed[len(header) + len(pieces[0]) - 1] ^= 1
        cases.append(bytes(changed))
        changed = bytearray(self.envelope)
        changed[len(archive.MAGIC) + 4 + 10] ^= 1
        cases.append(bytes(changed))
        changed = bytearray(self.envelope)
        changed[-1] ^= 1
        cases.append(bytes(changed))
        for item in cases:
            with self.subTest(index=cases.index(item)):
                with self.assertRaises(ValueError):
                    self._open(item)
        self.assertEqual(tail[:4], archive.END_MARKER)

    def test_truncation_and_extra_trailing_data_refused(self) -> None:
        header, pieces, _ = self._pieces()
        for item in (self.envelope[:-1], header + pieces[0],
                self.envelope + b"extra"):
            with self.subTest(length=len(item)):
                with self.assertRaises(ValueError):
                    self._open(item)

    def test_reordered_or_duplicated_chunk_refused(self) -> None:
        header, pieces, tail = self._pieces()
        for item in (header + pieces[1] + pieces[0] + pieces[2] + tail,
                header + pieces[0] + pieces[0] + pieces[2] + tail):
            with self.subTest(length=len(item)):
                with self.assertRaisesRegex(ValueError, "order or size"):
                    self._open(item)

    def test_chunk_size_and_header_version_drift_refused(self) -> None:
        header, _, _ = self._pieces()
        changed = bytearray(self.envelope)
        changed[len(header) + 7] ^= 1
        with self.assertRaisesRegex(ValueError, "order or size"):
            self._open(bytes(changed))
        changed = bytearray(self.envelope)
        changed[len(archive.MAGIC) - 2] ^= 1
        with self.assertRaises(ValueError):
            self._open(bytes(changed))

    def test_canonical_manifest_digest_forgery_fails_aad(self) -> None:
        header, pieces, tail = self._pieces()
        original = json.loads(header[len(archive.MAGIC) + 4:])
        digest = original["plaintextSha256"]
        original["plaintextSha256"] = ("0" if digest[0] != "0" else "1") + digest[1:]
        encoded = json.dumps(original, ensure_ascii=True, sort_keys=True,
            separators=(",", ":")).encode("ascii")
        forged = archive.MAGIC + struct.pack(">I", len(encoded)) + encoded
        with self.assertRaisesRegex(ValueError, "authentication failed"):
            self._open(forged + b"".join(pieces) + tail)
        original["version"] = 3
        encoded = json.dumps(original, ensure_ascii=True, sort_keys=True,
            separators=(",", ":")).encode("ascii")
        forged = archive.MAGIC + struct.pack(">I", len(encoded)) + encoded
        with self.assertRaisesRegex(ValueError, "version or sizes"):
            self._open(forged + b"".join(pieces) + tail)

    def test_bad_input_and_key_length_refused(self) -> None:
        with self.assertRaises(ValueError):
            archive.seal_archive(b"not custom", key_id=self.key_id,
                context_sha256=self.context, key_provider=self.provider)
        with self.assertRaisesRegex(ValueError, "invalid length"):
            self._open(self.envelope, _SyntheticKeys(b"too short"))

    def test_size_cap_refuses_seal_and_open(self) -> None:
        with mock.patch.object(archive, "MAX_PLAINTEXT_BYTES",
                len(self.plaintext) - 1):
            with self.assertRaisesRegex(ValueError, "bounded custom dump"):
                archive.seal_archive(self.plaintext, key_id=self.key_id,
                    context_sha256=self.context, key_provider=self.provider)
            with self.assertRaises(ValueError):
                self._open(self.envelope)


if __name__ == "__main__":
    unittest.main()

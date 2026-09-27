"""Synthetic file/pipe tests for the unadopted v2-stream-v1 archive layout."""

from __future__ import annotations

import hashlib
from io import BytesIO
import os
from pathlib import Path
import secrets
import struct
import sys
import tempfile
import tracemalloc
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import protected_ai_archive_v2 as old
import protected_ai_archive_v2_stream as stream


class Keys:
    def __init__(self, value: bytes):
        self.value = value
        self.calls: list[tuple[str, str]] = []

    def resolve_key(self, key_id: str, purpose: str) -> bytes:
        self.calls.append((key_id, purpose))
        return self.value


class GeneratedStream:
    def __init__(self, total: int):
        self.total = total
        self.offset = 0

    def read(self, count: int) -> bytes:
        size = min(count, self.total - self.offset)
        if size <= 0:
            return b""
        prefix = b"PGDMP" if self.offset == 0 else b""
        result = prefix + b"A" * (size - len(prefix))
        self.offset += size
        return result


class CountingSink:
    def __init__(self):
        self.count = 0
        self.sha = hashlib.sha256()

    def write(self, value: bytes) -> int:
        self.count += len(value)
        self.sha.update(value)
        return len(value)

    def clear(self) -> None:
        self.count = 0
        self.sha = hashlib.sha256()


class StreamArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key_id = "synthetic_stream_key"
        self.context = hashlib.sha256(b"synthetic owner and ACL roots").hexdigest()
        self.keys = Keys(secrets.token_bytes(32))

    def _seal(self, root: Path, plaintext: bytes) -> Path:
        path = root / "protected.dump.v2s1.aead"
        stream.seal_reader_to_file(BytesIO(plaintext), path,
            key_id=self.key_id, context_sha256=self.context,
            key_provider=self.keys)
        return path

    def _open(self, path: Path, keys: Keys | None = None):
        return stream.open_verified_stream(path,
            expected_key_id=self.key_id,
            expected_context_sha256=self.context,
            key_provider=keys or self.keys)

    @staticmethod
    def _copy_test(verified, sink):
        def discard():
            if isinstance(sink, BytesIO):
                sink.seek(0)
                sink.truncate(0)
            else:
                sink.clear()
        return verified.copy_to_test_sink(sink, discard_on_failure=discard)

    @staticmethod
    def _records(envelope: bytes):
        size = struct.unpack_from(">I", envelope, len(stream.MAGIC))[0]
        start = len(stream.MAGIC) + 4 + size
        records = []
        for _ in range(3):
            length = struct.unpack_from(">I", envelope, start + 5)[0]
            end = start + 9 + length + stream.TAG_BYTES
            records.append(envelope[start:end])
            start = end
        return envelope[:len(stream.MAGIC) + 4 + size], records, envelope[start:]

    def test_two_pass_round_trip_emits_only_after_first_verification(self):
        plain = b"PGDMP" + secrets.token_bytes(stream.CHUNK_BYTES * 2 + 23)
        with tempfile.TemporaryDirectory(prefix="protected-v2-stream-") as tmp:
            root = Path(tmp)
            path = self._seal(root, plain)
            self.assertEqual([item.name for item in root.iterdir()], [path.name])
            self.assertFalse(path.read_bytes().startswith(b"PGDMP"))
            output = BytesIO()
            with self._open(path) as verified:
                self.assertEqual(verified.evidence.chunk_count, 3)
                self.assertEqual(output.getvalue(), b"")
                self.assertEqual(self._copy_test(verified, output), verified.evidence)
                self.assertEqual(output.getvalue(), plain)
                with self.assertRaises(stream.ArchiveInvalid):
                    self._copy_test(verified, output)
            self.assertEqual(output.getvalue(), b"")

    def test_wrong_key_context_tamper_truncation_reorder_duplicate_fail(self):
        plain = b"PGDMP" + secrets.token_bytes(stream.CHUNK_BYTES * 2 + 7)
        with tempfile.TemporaryDirectory(prefix="protected-v2-stream-") as tmp:
            root = Path(tmp)
            path = self._seal(root, plain)
            with self.assertRaises(stream.ArchiveInvalid):
                self._open(path, Keys(secrets.token_bytes(32)))
            with self.assertRaises(stream.ArchiveInvalid):
                stream.open_verified_stream(path, expected_key_id=self.key_id,
                    expected_context_sha256="0" * 64,
                    key_provider=self.keys)
            envelope = path.read_bytes()
            header, records, footer = self._records(envelope)
            self.assertIn(b'"nonceSeed":"', header)
            self.assertIn(b'"plaintextSha256":"', footer)
            header_changed = bytearray(header)
            header_nonce = header.find(b'"nonceSeed":"') + len(b'"nonceSeed":"')
            header_changed[header_nonce] = (
                ord("0") if header_changed[header_nonce] != ord("0") else ord("1"))
            footer_changed = bytearray(footer)
            footer_digest = footer.find(b'"plaintextSha256":"') + len(
                b'"plaintextSha256":"')
            footer_changed[footer_digest] = (
                ord("0") if footer_changed[footer_digest] != ord("0") else ord("1"))
            cases = {
                "truncated": envelope[:-1],
                "reordered": header + records[1] + records[0] + records[2] + footer,
                "duplicate": header + records[0] + records[0] + records[2] + footer,
                "header_manifest_tampered": bytes(header_changed) +
                    b"".join(records) + footer,
                "footer_manifest_tampered": header + b"".join(records) +
                    bytes(footer_changed),
                "tampered": envelope[:len(header) + 9] + bytes((
                    envelope[len(header) + 9] ^ 1,)) + envelope[len(header) + 10:],
                "extra": envelope + b"x",
            }
            for label, damaged in cases.items():
                candidate = root / (label + ".aead")
                candidate.write_bytes(damaged)
                with self.subTest(label=label), self.assertRaises(stream.ArchiveInvalid):
                    self._open(candidate)

    def test_second_pass_detects_changed_file_before_plaintext(self):
        plain = b"PGDMP" + b"a" * (stream.CHUNK_BYTES + 9)
        with tempfile.TemporaryDirectory(prefix="protected-v2-stream-") as tmp:
            path = self._seal(Path(tmp), plain)
            with self._open(path) as verified:
                with path.open("r+b") as changed:
                    changed.seek(0, 2)
                    changed.write(b"x")
                output = BytesIO()
                with self.assertRaises(stream.ArchiveInvalid):
                    self._copy_test(verified, output)
                self.assertEqual(output.getvalue(), b"")

    def test_same_handle_toctou_never_completes_transactional_process(self):
        plain = b"PGDMP" + secrets.token_bytes(stream.CHUNK_BYTES * 2 + 31)
        with tempfile.TemporaryDirectory(prefix="protected-v2-stream-") as tmp:
            root = Path(tmp)
            path = self._seal(root, plain)
            digest_file = root / "should-not-commit.txt"
            with self._open(path) as verified:
                original = path.stat()
                header, records, _ = self._records(path.read_bytes())
                third_cipher_offset = (len(header) + len(records[0]) +
                    len(records[1]) + 9)
                with path.open("r+b") as changed:
                    changed.seek(third_cipher_offset)
                    old_byte = changed.read(1)
                    changed.seek(third_cipher_offset)
                    changed.write(bytes((old_byte[0] ^ 1,)))
                    changed.flush()
                    os.fsync(changed.fileno())
                os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
                script = ("import hashlib,os,pathlib,sys;"
                    "payload=sys.stdin.buffer.read();"
                    "pathlib.Path(os.environ['RESTORE_DIGEST']).write_text("
                    "hashlib.sha256(payload).hexdigest())")
                command = [sys.executable, "-c", script,
                    "--single-transaction", "--exit-on-error"]
                with self.assertRaisesRegex(stream.ArchiveInvalid,
                        "chunk authentication failed"):
                    verified.copy_to_transactional_process(command,
                        env={**os.environ,"RESTORE_DIGEST":str(digest_file)},
                        timeout_seconds=15)
            self.assertFalse(digest_file.exists())

    def test_default_provider_and_old_layout_remain_closed(self):
        plain = b"PGDMP" + b"a" * 100
        with tempfile.TemporaryDirectory(prefix="protected-v2-stream-") as tmp:
            root = Path(tmp)
            with self.assertRaises(stream.NotConfigured):
                stream.seal_reader_to_file(BytesIO(plain), root / "closed.aead",
                    key_id=self.key_id, context_sha256=self.context)
            self.assertEqual(list(root.iterdir()), [])
            old_bytes = old.seal_archive(plain, key_id=self.key_id,
                context_sha256=self.context, key_provider=self.keys)
            old_path = root / "old.aead"
            old_path.write_bytes(old_bytes)
            with self.assertRaises(stream.ArchiveInvalid):
                self._open(old_path)
            new_path = self._seal(root, plain)
            with self.assertRaises(ValueError):
                old.open_archive(new_path.read_bytes(),
                    expected_key_id=self.key_id,
                    expected_context_sha256=self.context,
                    key_provider=self.keys)

    def test_large_generated_stream_has_bounded_memory(self):
        total = 80 * 1024 * 1024 + 17  # Larger than the whole-buffer v2 cap.
        with tempfile.TemporaryDirectory(prefix="protected-v2-stream-") as tmp:
            root = Path(tmp)
            tracemalloc.start()
            try:
                evidence = stream.seal_reader_to_file(GeneratedStream(total),
                    root / "large.aead", key_id=self.key_id,
                    context_sha256=self.context, key_provider=self.keys)
                with self._open(root / "large.aead") as verified:
                    sink = CountingSink()
                    self._copy_test(verified, sink)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertEqual((evidence.plaintext_bytes,sink.count),
                (total,total))
            self.assertEqual(sink.sha.hexdigest(), evidence.plaintext_sha256)
            self.assertLess(peak, 24 * 1024 * 1024)
            self.assertEqual([item.name for item in root.iterdir()], ["large.aead"])

    def test_failed_source_process_removes_partial_encrypted_file(self):
        with tempfile.TemporaryDirectory(prefix="protected-v2-stream-") as tmp:
            root = Path(tmp)
            path = root / "failed.aead"
            command = [sys.executable, "-c",
                "import sys;sys.stdout.buffer.write(b'PGDMP'+b'x'*65536);sys.exit(7)"]
            with self.assertRaises(stream.ArchiveProcessError):
                stream.seal_process_stdout(command, path,
                    key_id=self.key_id, context_sha256=self.context,
                    key_provider=self.keys, timeout_seconds=15)
            self.assertEqual(list(root.iterdir()), [])

    def test_size_limit_and_source_timeout_remove_only_partial_ciphertext(self):
        with tempfile.TemporaryDirectory(prefix="protected-v2-stream-") as tmp:
            root = Path(tmp)
            with mock.patch.object(stream, "MAX_PLAINTEXT_BYTES",
                    stream.CHUNK_BYTES):
                with self.assertRaises(stream.ArchiveInvalid):
                    stream.seal_reader_to_file(
                        GeneratedStream(stream.CHUNK_BYTES * 2),
                        root / "oversize.aead", key_id=self.key_id,
                        context_sha256=self.context, key_provider=self.keys)
            self.assertEqual(list(root.iterdir()), [])
            command = [sys.executable, "-c",
                "import sys,time;sys.stdout.buffer.write(b'PGDMP');"
                "sys.stdout.buffer.flush();time.sleep(10)"]
            with self.assertRaises(stream.ArchiveProcessError):
                stream.seal_process_stdout(command, root / "timed-out.aead",
                    key_id=self.key_id, context_sha256=self.context,
                    key_provider=self.keys, timeout_seconds=1)
            self.assertEqual(list(root.iterdir()), [])

    def test_successful_source_pipe_never_writes_plaintext_dump(self):
        with tempfile.TemporaryDirectory(prefix="protected-v2-stream-") as tmp:
            root = Path(tmp)
            path = root / "from-pipe.aead"
            command = [sys.executable, "-c",
                "import sys;sys.stdout.buffer.write(b'PGDMP'+b'x'*1500000)"]
            evidence = stream.seal_process_stdout(command, path,
                key_id=self.key_id, context_sha256=self.context,
                key_provider=self.keys, timeout_seconds=15)
            sink = CountingSink()
            with self._open(path) as verified:
                self._copy_test(verified, sink)
            self.assertEqual((evidence.plaintext_bytes,sink.count),
                (1_500_005,1_500_005))
            self.assertEqual([item.name for item in root.iterdir()], [path.name])

    def test_verified_input_only_feeds_transactional_process(self):
        plain = b"PGDMP" + secrets.token_bytes(stream.CHUNK_BYTES + 31)
        with tempfile.TemporaryDirectory(prefix="protected-v2-stream-") as tmp:
            root = Path(tmp)
            path = self._seal(root, plain)
            digest_file = root / "restore-digest.txt"
            script = ("import hashlib,os,pathlib,sys;"
                "payload=sys.stdin.buffer.read();"
                "pathlib.Path(os.environ['RESTORE_DIGEST']).write_text("
                "hashlib.sha256(payload).hexdigest())")
            command = [sys.executable, "-c", script,
                "--single-transaction", "--exit-on-error"]
            with self._open(path) as verified:
                with self.assertRaises(ValueError):
                    verified.copy_to_transactional_process(command[:-1])
                result = verified.copy_to_transactional_process(command,
                    env={**os.environ,"RESTORE_DIGEST":str(digest_file)},
                    timeout_seconds=15)
            self.assertEqual(result.plaintext_bytes, len(plain))
            self.assertEqual(digest_file.read_text(),
                hashlib.sha256(plain).hexdigest())
            self.assertFalse(any(item.suffix == ".dump" for item in root.iterdir()))

    def test_failed_transactional_restore_process_is_not_success(self):
        plain = b"PGDMP" + b"x" * (stream.CHUNK_BYTES + 7)
        with tempfile.TemporaryDirectory(prefix="protected-v2-stream-") as tmp:
            root = Path(tmp)
            path = self._seal(root, plain)
            command = [sys.executable, "-c",
                "import sys;sys.stdin.buffer.read();sys.exit(7)",
                "--single-transaction", "--exit-on-error"]
            with self._open(path) as verified:
                with self.assertRaises(stream.ArchiveProcessError):
                    verified.copy_to_transactional_process(command,
                        timeout_seconds=15)
            self.assertEqual([item.name for item in root.iterdir()], [path.name])


if __name__ == "__main__":
    unittest.main()

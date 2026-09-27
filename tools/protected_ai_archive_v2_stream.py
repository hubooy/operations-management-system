"""Bounded streaming AES-GCM archive candidate, isolated from formal backup.

On-wire format ``v2-stream-v1`` is intentionally incompatible with the older
64 MiB in-memory ``protected_ai_archive_v2`` format. No key store, PostgreSQL
operator, or formal backup/restore route is installed by this module.

Plaintext remains in process memory one chunk at a time. Restoration verifies
the whole encrypted file before any plaintext is emitted, then authenticates
it again from the same open handle while streaming to a transactional sink.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import struct
import subprocess
import threading
from typing import BinaryIO, Callable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from protected_ai_archive_v2 import (
    DEFAULT_KEY_PROVIDER, KeyProvider, NotConfigured,
)


MAGIC = b"TERUISI-PROTECTED-AI-V2-STREAM-1\x00"
FORMAT = "teruisi-protected-ai-archive-v2-stream-v1"
VERSION = 2
LAYOUT = "stream-v1"
CIPHER = "AES-256-GCM"
CHUNK_BYTES = 1024 * 1024
MAX_PLAINTEXT_BYTES = 1 << 40  # 1 TiB; never an unbounded backup.
MAX_CHUNKS = MAX_PLAINTEXT_BYTES // CHUNK_BYTES
MAX_HEADER_BYTES = 1024
MAX_FOOTER_BYTES = 512
TAG_BYTES = 16
DOMAIN = b"teruisi:protected-ai:stream:v2:1\x00"
KEY_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX24 = re.compile(r"[0-9a-f]{24}\Z")
HEADER_FIELDS = frozenset({"format", "version", "layout", "cipher",
    "keyId", "contextSha256", "chunkBytes", "nonceSeed"})
FOOTER_FIELDS = frozenset({"chunkCount", "plaintextBytes",
    "plaintextSha256", "ciphertextSha256"})


class ArchiveInvalid(ValueError):
    """The encrypted archive is incomplete, changed, or unauthenticated."""


class ArchiveProcessError(RuntimeError):
    """The bounded source or restore process failed."""


@dataclass(frozen=True)
class StreamEvidence:
    format: str
    chunk_count: int
    plaintext_bytes: int
    plaintext_sha256: str
    ciphertext_sha256: str


def _canonical(value: dict[str, object]) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("ascii")


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ArchiveInvalid("duplicate archive metadata field")
        result[key] = value
    return result


def _decode(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(raw, object_pairs_hook=_unique_pairs)
    except (ValueError, UnicodeError, TypeError) as error:
        raise ArchiveInvalid("archive metadata is invalid") from error
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise ArchiveInvalid("archive metadata is not canonical")
    return value


def _validate_header(value: dict[str, object]) -> None:
    if (set(value) != HEADER_FIELDS or value["format"] != FORMAT
            or type(value["version"]) is not int or value["version"] != VERSION
            or value["layout"] != LAYOUT or value["cipher"] != CIPHER
            or type(value["chunkBytes"]) is not int
            or value["chunkBytes"] != CHUNK_BYTES):
        raise ArchiveInvalid("archive header version or fields are invalid")
    for name, pattern in (("keyId", KEY_ID), ("contextSha256", HEX64),
            ("nonceSeed", HEX24)):
        item = value[name]
        if not isinstance(item, str) or pattern.fullmatch(item) is None:
            raise ArchiveInvalid("archive header identity is invalid")


def _validate_footer(value: dict[str, object]) -> None:
    if (set(value) != FOOTER_FIELDS
            or type(value["chunkCount"]) is not int
            or type(value["plaintextBytes"]) is not int
            or not 1 <= value["chunkCount"] <= MAX_CHUNKS
            or not 5 <= value["plaintextBytes"] <= MAX_PLAINTEXT_BYTES
            or value["chunkCount"] !=
                (value["plaintextBytes"] + CHUNK_BYTES - 1) // CHUNK_BYTES):
        raise ArchiveInvalid("archive footer size or chunk count is invalid")
    for name in ("plaintextSha256", "ciphertextSha256"):
        item = value[name]
        if not isinstance(item, str) or HEX64.fullmatch(item) is None:
            raise ArchiveInvalid("archive footer digest is invalid")


def _key(provider: KeyProvider, key_id: str, purpose: str) -> bytes:
    value = provider.resolve_key(key_id, purpose)  # type: ignore[arg-type]
    if not isinstance(value, bytes) or len(value) != 32:
        raise ValueError("archive key must have exactly 256 bits")
    return value


def _nonce(seed: bytes, index: int) -> bytes:
    if len(seed) != 12 or not 0 <= index < (1 << 32):
        raise ArchiveInvalid("archive nonce index is invalid")
    return seed[:8] + (int.from_bytes(seed[8:], "big") ^ index).to_bytes(4, "big")


def _read_exact(source: BinaryIO, size: int) -> bytes:
    pieces = []
    remaining = size
    while remaining:
        part = source.read(remaining)
        if not isinstance(part, bytes) or not part:
            raise ArchiveInvalid("encrypted archive is truncated")
        pieces.append(part)
        remaining -= len(part)
    return b"".join(pieces)


def _read_source_chunk(source: BinaryIO) -> bytes:
    pieces = []
    remaining = CHUNK_BYTES
    while remaining:
        part = source.read(remaining)
        if not isinstance(part, bytes):
            raise ArchiveInvalid("source stream returned non-bytes")
        if not part:
            break
        pieces.append(part)
        remaining -= len(part)
    return b"".join(pieces)


def _write_all(sink: BinaryIO, data: bytes) -> None:
    if sink.write(data) != len(data):
        raise OSError("short encrypted archive write")


def _header_bytes(key_id: str, context_sha256: str, seed: bytes) -> bytes:
    value = {"format": FORMAT, "version": VERSION, "layout": LAYOUT,
        "cipher": CIPHER, "keyId": key_id,
        "contextSha256": context_sha256, "chunkBytes": CHUNK_BYTES,
        "nonceSeed": seed.hex()}
    _validate_header(value)
    encoded = _canonical(value)
    if len(encoded) > MAX_HEADER_BYTES:
        raise ArchiveInvalid("archive header is too large")
    return MAGIC + struct.pack(">I", len(encoded)) + encoded


def _seal_to_temp(source: BinaryIO, output: Path, key: bytes,
        key_id: str, context_sha256: str,
        source_complete: Callable[[], None] | None) -> StreamEvidence:
    if output.exists() or not output.parent.is_dir():
        raise FileExistsError("archive target exists or parent is missing")
    temporary = output.with_name(output.name + ".incomplete-" +
        secrets.token_hex(8))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
        getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as sink:
            seed = secrets.token_bytes(12)
            header = _header_bytes(key_id, context_sha256, seed)
            cipher = AESGCM(key)
            plaintext_digest = hashlib.sha256()
            ciphertext_digest = hashlib.sha256()
            _write_all(sink, header)
            ciphertext_digest.update(header)
            total = 0
            count = 0
            while True:
                chunk = _read_source_chunk(source)
                if not chunk:
                    break
                if count >= MAX_CHUNKS or total + len(chunk) > MAX_PLAINTEXT_BYTES:
                    raise ArchiveInvalid("archive plaintext exceeds fixed limit")
                if count == 0 and not chunk.startswith(b"PGDMP"):
                    raise ArchiveInvalid("source is not a PostgreSQL custom dump")
                record = b"D" + struct.pack(">II", count, len(chunk))
                encrypted = cipher.encrypt(_nonce(seed, count), chunk,
                    DOMAIN + header + record)
                _write_all(sink, record)
                _write_all(sink, encrypted)
                ciphertext_digest.update(record)
                ciphertext_digest.update(encrypted)
                plaintext_digest.update(chunk)
                total += len(chunk)
                count += 1
            if total < 5:
                raise ArchiveInvalid("source custom dump is empty")
            footer = {"chunkCount": count, "plaintextBytes": total,
                "plaintextSha256": plaintext_digest.hexdigest(),
                "ciphertextSha256": ciphertext_digest.hexdigest()}
            _validate_footer(footer)
            encoded_footer = _canonical(footer)
            if len(encoded_footer) > MAX_FOOTER_BYTES:
                raise ArchiveInvalid("archive footer is too large")
            frame = b"F" + struct.pack(">I", len(encoded_footer)) + encoded_footer
            tag = cipher.encrypt(_nonce(seed, count), b"",
                DOMAIN + header + frame)
            _write_all(sink, frame)
            _write_all(sink, tag)
            sink.flush()
            os.fsync(sink.fileno())
            if source_complete is not None:
                source_complete()
        if output.exists():
            raise FileExistsError("archive target appeared during seal")
        os.rename(temporary, output)
        return StreamEvidence(FORMAT, count, total,
            plaintext_digest.hexdigest(), ciphertext_digest.hexdigest())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def seal_reader_to_file(source: BinaryIO, output: Path, *, key_id: str,
        context_sha256: str,
        key_provider: KeyProvider = DEFAULT_KEY_PROVIDER,
        source_complete: Callable[[], None] | None = None) -> StreamEvidence:
    """Publish only a complete encrypted archive; never a plaintext dump."""
    # Resolve the key before opening any output. Default custody fails closed.
    header = _header_bytes(key_id, context_sha256, b"\x00" * 12)
    del header
    key = _key(key_provider, key_id, "seal")
    return _seal_to_temp(source, Path(output), key, key_id,
        context_sha256, source_complete)


def seal_process_stdout(command: list[str | os.PathLike[str]], output: Path,
        *, key_id: str, context_sha256: str,
        key_provider: KeyProvider = DEFAULT_KEY_PROVIDER,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 1800) -> StreamEvidence:
    """Bound a pg_dump-like subprocess and remove its partial encrypted file."""
    if not command or not 1 <= timeout_seconds <= 86400:
        raise ValueError("archive source command or timeout is invalid")
    _header_bytes(key_id, context_sha256, b"\x00" * 12)
    key = _key(key_provider, key_id, "seal")
    process = subprocess.Popen([str(item) for item in command],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, shell=False, env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    timer = threading.Timer(timeout_seconds,
        lambda: process.kill() if process.poll() is None else None)
    timer.daemon = True
    timer.start()
    try:
        if process.stdout is None:
            raise ArchiveProcessError("source stdout is unavailable")

        def completed() -> None:
            if process.wait(timeout=timeout_seconds) != 0:
                raise ArchiveProcessError("archive source process failed")

        return _seal_to_temp(process.stdout, Path(output), key,
            key_id, context_sha256, completed)
    finally:
        timer.cancel()
        if process.stdout is not None:
            process.stdout.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=30)


def _parse_header(source: BinaryIO) -> tuple[bytes, dict[str, object]]:
    if _read_exact(source, len(MAGIC)) != MAGIC:
        raise ArchiveInvalid("archive format is not v2-stream-v1")
    length_raw = _read_exact(source, 4)
    length = struct.unpack(">I", length_raw)[0]
    if not 2 <= length <= MAX_HEADER_BYTES:
        raise ArchiveInvalid("archive header length is invalid")
    encoded = _read_exact(source, length)
    value = _decode(encoded)
    _validate_header(value)
    return MAGIC + length_raw + encoded, value


def _scan(source: BinaryIO, key: bytes, *, expected_key_id: str,
        expected_context_sha256: str, sink: BinaryIO | None = None,
        expected: StreamEvidence | None = None) -> StreamEvidence:
    source.seek(0)
    header, metadata = _parse_header(source)
    if (metadata["keyId"] != expected_key_id
            or metadata["contextSha256"] != expected_context_sha256):
        raise ArchiveInvalid("archive expected identity does not match")
    seed = bytes.fromhex(metadata["nonceSeed"])
    cipher = AESGCM(key)
    plaintext_digest = hashlib.sha256()
    ciphertext_digest = hashlib.sha256(header)
    total = 0
    count = 0
    short_seen = False
    pending: bytes | None = None
    while True:
        marker = _read_exact(source, 1)
        if marker == b"D":
            if short_seen or count >= MAX_CHUNKS:
                raise ArchiveInvalid("archive chunk follows the final short chunk")
            record = marker + _read_exact(source, 8)
            index, size = struct.unpack(">II", record[1:])
            if index != count or not 1 <= size <= CHUNK_BYTES:
                raise ArchiveInvalid("archive chunk order or length is invalid")
            if total + size > MAX_PLAINTEXT_BYTES:
                raise ArchiveInvalid("archive plaintext exceeds fixed limit")
            ciphertext = _read_exact(source, size + TAG_BYTES)
            try:
                chunk = cipher.decrypt(_nonce(seed, index), ciphertext,
                    DOMAIN + header + record)
            except InvalidTag:
                raise ArchiveInvalid("archive chunk authentication failed") from None
            if count == 0 and not chunk.startswith(b"PGDMP"):
                raise ArchiveInvalid("archive payload is not a custom dump")
            if sink is not None and pending is not None:
                _write_all(sink, pending)
            pending = chunk
            plaintext_digest.update(chunk)
            ciphertext_digest.update(record)
            ciphertext_digest.update(ciphertext)
            total += size
            count += 1
            short_seen = size < CHUNK_BYTES
            continue
        if marker != b"F":
            raise ArchiveInvalid("archive final frame is missing")
        length_raw = _read_exact(source, 4)
        length = struct.unpack(">I", length_raw)[0]
        if not 2 <= length <= MAX_FOOTER_BYTES:
            raise ArchiveInvalid("archive footer length is invalid")
        encoded = _read_exact(source, length)
        footer = _decode(encoded)
        _validate_footer(footer)
        frame = marker + length_raw + encoded
        tag = _read_exact(source, TAG_BYTES)
        try:
            cipher.decrypt(_nonce(seed, count), tag, DOMAIN + header + frame)
        except InvalidTag:
            raise ArchiveInvalid("archive final authentication failed") from None
        if source.read(1) != b"":
            raise ArchiveInvalid("archive has trailing bytes")
        result = StreamEvidence(FORMAT, count, total,
            plaintext_digest.hexdigest(), ciphertext_digest.hexdigest())
        if (count != footer["chunkCount"] or total != footer["plaintextBytes"]
                or not hmac.compare_digest(result.plaintext_sha256,
                    footer["plaintextSha256"])
                or not hmac.compare_digest(result.ciphertext_sha256,
                    footer["ciphertextSha256"])
                or (expected is not None and result != expected)):
            raise ArchiveInvalid("archive final counts or digest differ")
        if sink is not None and pending is not None:
            _write_all(sink, pending)
        return result


def _fingerprint(stat: os.stat_result) -> tuple[int, int, int, int]:
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


class VerifiedStream:
    """Own one encrypted file handle across both authenticated passes."""

    def __init__(self, source: BinaryIO, key: bytes, key_id: str,
            context_sha256: str, evidence: StreamEvidence,
            fingerprint: tuple[int, int, int, int]):
        self._source = source
        self._key = key
        self._key_id = key_id
        self._context = context_sha256
        self.evidence = evidence
        self._fingerprint = fingerprint
        self._used = False

    def __enter__(self) -> "VerifiedStream":
        return self

    def __exit__(self, *_: object) -> None:
        self._source.close()

    def _copy_to_sink(self, sink: BinaryIO) -> StreamEvidence:
        if self._used:
            raise ArchiveInvalid("verified archive may be emitted only once")
        self._used = True
        if _fingerprint(os.fstat(self._source.fileno())) != self._fingerprint:
            raise ArchiveInvalid("encrypted archive changed after first pass")
        result = _scan(self._source, self._key,
            expected_key_id=self._key_id,
            expected_context_sha256=self._context,
            sink=sink, expected=self.evidence)
        if _fingerprint(os.fstat(self._source.fileno())) != self._fingerprint:
            raise ArchiveInvalid("encrypted archive changed during second pass")
        return result

    def copy_to_test_sink(self, sink: BinaryIO, *,
            discard_on_failure: Callable[[], None]) -> StreamEvidence:
        """Nontransactional test hook; the caller must discard partial output.

        Production restore must use copy_to_transactional_process instead.
        """
        if not callable(discard_on_failure):
            raise ValueError("test sink requires a discard callback")
        try:
            return self._copy_to_sink(sink)
        except BaseException:
            discard_on_failure()
            raise

    def copy_to_transactional_process(self,
            command: list[str | os.PathLike[str]], *,
            env: dict[str, str] | None = None,
            timeout_seconds: int = 1800) -> StreamEvidence:
        """Feed verified bytes to one bounded pg_restore-style transaction.

        The caller still owns database identity, role, and rollback checks.
        Requiring the exact transaction/error flags prevents a partial restore
        from being mistaken for a completed archive recovery.
        """
        args = [str(item) for item in command]
        if (not args or "--single-transaction" not in args
                or "--exit-on-error" not in args
                or not 1 <= timeout_seconds <= 86400):
            raise ValueError("transactional restore command is incomplete")
        process = subprocess.Popen(args, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            shell=False, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        timer = threading.Timer(timeout_seconds,
            lambda: process.kill() if process.poll() is None else None)
        timer.daemon = True
        timer.start()
        completed = False
        try:
            if process.stdin is None:
                raise ArchiveProcessError("restore stdin is unavailable")
            evidence = self._copy_to_sink(process.stdin)
            process.stdin.close()
            if process.wait(timeout=timeout_seconds) != 0:
                raise ArchiveProcessError("transactional restore process failed")
            completed = True
            return evidence
        finally:
            timer.cancel()
            # On failed second-pass authentication, closing stdin first would
            # deliver a clean EOF to a child that has consumed partial bytes.
            # Kill and reap it before any pipe close can let it commit.
            if not completed and process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
            process.wait(timeout=30)
            if process.stdin is not None and not process.stdin.closed:
                try:
                    process.stdin.close()
                except OSError:
                    pass


def open_verified_stream(path: Path, *, expected_key_id: str,
        expected_context_sha256: str,
        key_provider: KeyProvider = DEFAULT_KEY_PROVIDER) -> VerifiedStream:
    """First pass authenticates the entire ciphertext without emitting bytes."""
    source = open(path, "rb")
    try:
        stat = _fingerprint(os.fstat(source.fileno()))
        header, value = _parse_header(source)
        del header
        if (value["keyId"] != expected_key_id
                or value["contextSha256"] != expected_context_sha256):
            raise ArchiveInvalid("archive expected identity does not match")
        key = _key(key_provider, expected_key_id, "open")
        evidence = _scan(source, key, expected_key_id=expected_key_id,
            expected_context_sha256=expected_context_sha256)
        if _fingerprint(os.fstat(source.fileno())) != stat:
            raise ArchiveInvalid("encrypted archive changed during verification")
        return VerifiedStream(source, key, expected_key_id,
            expected_context_sha256, evidence, stat)
    except BaseException:
        source.close()
        raise


__all__ = ["ArchiveInvalid", "ArchiveProcessError", "StreamEvidence",
    "VerifiedStream", "seal_reader_to_file", "seal_process_stdout",
    "open_verified_stream", "NotConfigured", "FORMAT", "CHUNK_BYTES"]

"""Pure, bounded protected archive v2 format; no production key or operator.

The module never writes a plaintext file. It authenticates the complete archive
before returning any plaintext. Its 64 MiB limit makes it a contract and an
isolated-test building block, not a large production database backup engine.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import secrets
import struct
from typing import Literal, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


MAGIC = b"TERUISI-PROTECTED-AI-ARCHIVE-V2\x00"
FORMAT = "teruisi-protected-ai-archive-v2"
VERSION = 2
CIPHER = "AES-256-GCM"
CHUNK_BYTES = 64 * 1024
MAX_PLAINTEXT_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024
MAX_CHUNKS = MAX_PLAINTEXT_BYTES // CHUNK_BYTES
END_MARKER = b"END2"
TAG_BYTES = 16
NONCE_SEED_BYTES = 12
MANIFEST_FIELDS = frozenset({"format", "version", "cipher", "keyId",
    "contextSha256", "chunkBytes", "chunkCount", "plaintextBytes",
    "plaintextSha256", "nonceSeed"})
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX24 = re.compile(r"[0-9a-f]{24}\Z")
KEY_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


class NotConfigured(RuntimeError):
    """No approved archive key custody has been installed."""


class KeyProvider(Protocol):
    def resolve_key(self, key_id: str, purpose: Literal["seal", "open"]) -> bytes:
        """Return a 32-byte key without logging or exposing its value."""


class NotConfiguredKeyProvider:
    def resolve_key(self, key_id: str, purpose: Literal["seal", "open"]) -> bytes:
        raise NotConfigured("protected archive key provider is not configured")


DEFAULT_KEY_PROVIDER: KeyProvider = NotConfiguredKeyProvider()


@dataclass(frozen=True)
class VerifiedArchive:
    plaintext: bytes
    manifest: dict[str, object]


def _canonical(value: dict[str, object]) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("ascii")


def _key(provider: KeyProvider, key_id: str,
        purpose: Literal["seal", "open"]) -> bytes:
    value = provider.resolve_key(key_id, purpose)
    if not isinstance(value, bytes) or len(value) != 32:
        raise ValueError("protected archive key has invalid length")
    return value


def _expected_chunk_size(index: int, count: int, total: int) -> int:
    return CHUNK_BYTES if index < count - 1 else total - CHUNK_BYTES * (count - 1)


def _nonce(seed: bytes, index: int) -> bytes:
    if len(seed) != NONCE_SEED_BYTES or not 0 <= index < (1 << 32):
        raise ValueError("protected archive nonce identity is invalid")
    # A full random 96-bit seed separates archives; XOR makes every block's
    # nonce unique within this archive, including the authenticated end frame.
    suffix = int.from_bytes(seed[8:], "big") ^ index
    return seed[:8] + suffix.to_bytes(4, "big")


def _aad(header: bytes, kind: bytes, index: int, size: int) -> bytes:
    return b"teruisi:protected-ai:archive:v2\x00" + header + kind + struct.pack(
        ">II", index, size)


def _validate_manifest(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != MANIFEST_FIELDS:
        raise ValueError("protected archive manifest fields are invalid")
    manifest = value
    if (manifest["format"] != FORMAT or type(manifest["version"]) is not int
            or manifest["version"] != VERSION or manifest["cipher"] != CIPHER
            or type(manifest["chunkBytes"]) is not int
            or manifest["chunkBytes"] != CHUNK_BYTES
            or type(manifest["chunkCount"]) is not int
            or type(manifest["plaintextBytes"]) is not int):
        raise ValueError("protected archive manifest version or sizes are invalid")
    total = manifest["plaintextBytes"]
    count = manifest["chunkCount"]
    if (not 5 <= total <= MAX_PLAINTEXT_BYTES
            or count != (total + CHUNK_BYTES - 1) // CHUNK_BYTES
            or not 1 <= count <= MAX_CHUNKS):
        raise ValueError("protected archive manifest size does not match chunk count")
    for field, pattern in (("keyId", KEY_ID), ("contextSha256", HEX64),
            ("plaintextSha256", HEX64), ("nonceSeed", HEX24)):
        item = manifest[field]
        if not isinstance(item, str) or pattern.fullmatch(item) is None:
            raise ValueError("protected archive manifest identity is invalid")
    return manifest


def seal_archive(plaintext: bytes, *, key_id: str, context_sha256: str,
        key_provider: KeyProvider = DEFAULT_KEY_PROVIDER) -> bytes:
    """Seal one bounded PostgreSQL custom dump entirely in memory."""
    if (not isinstance(plaintext, bytes) or not plaintext.startswith(b"PGDMP")
            or not 5 <= len(plaintext) <= MAX_PLAINTEXT_BYTES):
        raise ValueError("protected archive input is not a bounded custom dump")
    digest = hashlib.sha256(plaintext).hexdigest()
    count = (len(plaintext) + CHUNK_BYTES - 1) // CHUNK_BYTES
    seed = secrets.token_bytes(NONCE_SEED_BYTES)
    manifest = _validate_manifest({
        "format": FORMAT, "version": VERSION, "cipher": CIPHER,
        "keyId": key_id, "contextSha256": context_sha256,
        "chunkBytes": CHUNK_BYTES, "chunkCount": count,
        "plaintextBytes": len(plaintext), "plaintextSha256": digest,
        "nonceSeed": seed.hex(),
    })
    key = _key(key_provider, key_id, "seal")
    encoded = _canonical(manifest)
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise ValueError("protected archive manifest is too large")
    header = MAGIC + struct.pack(">I", len(encoded)) + encoded
    cipher = AESGCM(key)
    output = bytearray(header)
    for index in range(count):
        chunk = plaintext[index * CHUNK_BYTES:(index + 1) * CHUNK_BYTES]
        record = struct.pack(">II", index, len(chunk))
        output.extend(record)
        output.extend(cipher.encrypt(_nonce(seed, index), chunk,
            _aad(header, b"DATA", index, len(chunk))))
    output.extend(END_MARKER)
    output.extend(cipher.encrypt(_nonce(seed, count), b"",
        _aad(header, b"FINAL", count, len(plaintext))))
    return bytes(output)


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("protected archive manifest has duplicate fields")
        result[key] = value
    return result


def open_archive(envelope: bytes, *, expected_key_id: str,
        expected_context_sha256: str,
        key_provider: KeyProvider = DEFAULT_KEY_PROVIDER) -> VerifiedArchive:
    """Return plaintext only after every chunk, final frame and digest validate."""
    maximum = len(MAGIC) + 4 + MAX_MANIFEST_BYTES + MAX_PLAINTEXT_BYTES + (
        MAX_CHUNKS * (8 + TAG_BYTES)) + len(END_MARKER) + TAG_BYTES
    if (not isinstance(envelope, bytes) or len(envelope) > maximum
            or len(envelope) < len(MAGIC) + 4 + 2 + 8 + TAG_BYTES + 4 + TAG_BYTES
            or not envelope.startswith(MAGIC)):
        raise ValueError("protected archive envelope identity or length is invalid")
    manifest_length = struct.unpack_from(">I", envelope, len(MAGIC))[0]
    if not 2 <= manifest_length <= MAX_MANIFEST_BYTES:
        raise ValueError("protected archive manifest length is invalid")
    manifest_start = len(MAGIC) + 4
    manifest_end = manifest_start + manifest_length
    if manifest_end > len(envelope):
        raise ValueError("protected archive manifest is truncated")
    encoded = envelope[manifest_start:manifest_end]
    try:
        manifest = _validate_manifest(json.loads(encoded,
            object_pairs_hook=_unique_pairs))
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("protected archive manifest is invalid") from exc
    if encoded != _canonical(manifest):
        raise ValueError("protected archive manifest is not canonical")
    if (manifest["keyId"] != expected_key_id
            or manifest["contextSha256"] != expected_context_sha256):
        raise ValueError("protected archive expected identity does not match")
    key = _key(key_provider, expected_key_id, "open")
    cipher = AESGCM(key)
    header = envelope[:manifest_end]
    seed = bytes.fromhex(manifest["nonceSeed"])
    count = manifest["chunkCount"]
    total = manifest["plaintextBytes"]
    offset = manifest_end
    plaintext = bytearray()
    for index in range(count):
        if offset + 8 > len(envelope):
            raise ValueError("protected archive chunk header is truncated")
        actual_index, size = struct.unpack_from(">II", envelope, offset)
        offset += 8
        expected_size = _expected_chunk_size(index, count, total)
        if actual_index != index or size != expected_size:
            raise ValueError("protected archive chunk order or size is invalid")
        end = offset + size + TAG_BYTES
        if end > len(envelope):
            raise ValueError("protected archive chunk is truncated")
        try:
            chunk = cipher.decrypt(_nonce(seed, index), envelope[offset:end],
                _aad(header, b"DATA", index, size))
        except InvalidTag:
            raise ValueError("protected archive chunk authentication failed") from None
        plaintext.extend(chunk)
        offset = end
    if (envelope[offset:offset + len(END_MARKER)] != END_MARKER
            or len(envelope) - offset != len(END_MARKER) + TAG_BYTES):
        raise ValueError("protected archive final frame is missing or trailing data exists")
    try:
        cipher.decrypt(_nonce(seed, count),
            envelope[offset + len(END_MARKER):],
            _aad(header, b"FINAL", count, total))
    except InvalidTag:
        raise ValueError("protected archive final authentication failed") from None
    if (len(plaintext) != total or not plaintext.startswith(b"PGDMP")
            or not hmac.compare_digest(hashlib.sha256(plaintext).hexdigest(),
                manifest["plaintextSha256"])):
        raise ValueError("protected archive plaintext digest is invalid")
    return VerifiedArchive(bytes(plaintext), manifest)

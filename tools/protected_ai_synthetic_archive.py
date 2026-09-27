"""Bounded, test-only authenticated envelope for synthetic PostgreSQL archives.

This deliberately has no production key provider or backup manifest contract.
The caller owns the random test key; neither key nor plaintext is written here.
"""

from __future__ import annotations

import hmac
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


MAGIC = b"TERUISI-AI-PG-TEST-ENC\x00"
VERSION = 1
NONCE_BYTES = 12
CONTEXT_BYTES = 32
TAG_BYTES = 16
MAX_PLAINTEXT_BYTES = 64 * 1024 * 1024
HEADER_BYTES = len(MAGIC) + 1 + CONTEXT_BYTES + NONCE_BYTES


def _key_and_context(key: bytes, context_digest: bytes) -> None:
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("synthetic archive requires a 256-bit key")
    if not isinstance(context_digest, bytes) or len(context_digest) != CONTEXT_BYTES:
        raise ValueError("synthetic archive context digest is invalid")


def encrypt(plaintext: bytes, key: bytes, context_digest: bytes) -> bytes:
    _key_and_context(key, context_digest)
    if (not isinstance(plaintext, bytes) or not plaintext.startswith(b"PGDMP")
            or not 5 <= len(plaintext) <= MAX_PLAINTEXT_BYTES):
        raise ValueError("synthetic input is not a bounded custom PostgreSQL archive")
    header = MAGIC + bytes((VERSION,)) + context_digest + secrets.token_bytes(NONCE_BYTES)
    return header + AESGCM(key).encrypt(header[-NONCE_BYTES:], plaintext, header)


def decrypt(envelope: bytes, key: bytes, context_digest: bytes) -> bytes:
    _key_and_context(key, context_digest)
    if (not isinstance(envelope, bytes)
            or not HEADER_BYTES + TAG_BYTES + 5 <= len(envelope)
            <= HEADER_BYTES + TAG_BYTES + MAX_PLAINTEXT_BYTES):
        raise ValueError("synthetic encrypted archive length is invalid")
    header = envelope[:HEADER_BYTES]
    if (not header.startswith(MAGIC)
            or header[len(MAGIC)] != VERSION
            or not hmac.compare_digest(
                header[len(MAGIC) + 1:len(MAGIC) + 1 + CONTEXT_BYTES],
                context_digest)):
        raise ValueError("synthetic encrypted archive identity is invalid")
    try:
        plaintext = AESGCM(key).decrypt(header[-NONCE_BYTES:],
            envelope[HEADER_BYTES:], header)
    except InvalidTag:
        raise ValueError("synthetic encrypted archive authentication failed") from None
    if not plaintext.startswith(b"PGDMP"):
        raise ValueError("decrypted payload is not a custom PostgreSQL archive")
    return plaintext

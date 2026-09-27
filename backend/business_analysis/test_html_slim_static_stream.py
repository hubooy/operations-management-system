"""Bounded synthetic NDJSON verification without whole-table row retention."""
import base64
import gzip
import hashlib
import importlib.util
from pathlib import Path
from unittest import TestCase

from .contracts import canonical


TOOL = Path(__file__).resolve().parents[2] / "tools" / "business-v10-slim-html-static-verify.py"
SPEC = importlib.util.spec_from_file_location("v10_slim_stream_verify", TOOL)
assert SPEC and SPEC.loader
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


def fixture(row):
    raw = (canonical(row) + "\n").encode()
    packed = gzip.compress(raw, mtime=0)
    return (base64.b64encode(packed).decode(), len(raw),
        hashlib.sha256(packed).hexdigest(), hashlib.sha256(raw).hexdigest())


class HtmlSlimStaticStreamTests(TestCase):
    def test_exact_row_and_empty_table(self):
        self.assertEqual(tool._verify_rows(*fixture(["合成", 2]), 1, 2), 1)
        empty = gzip.compress(b"", mtime=0)
        self.assertEqual(tool._verify_rows(base64.b64encode(empty).decode(), 0,
            hashlib.sha256(empty).hexdigest(), hashlib.sha256(b"").hexdigest(),
            0, 2), 0)

    def test_wrong_hash_count_width_and_expansion_fail(self):
        value = fixture(["合成", 2])
        for args in ((*value[:2], "0" * 64, *value[3:], 1, 2),
                (*value[:3], "0" * 64, 1, 2),
                (*value, 2, 2), (*value, 1, 3),
                (value[0], value[1]-1, *value[2:], 1, 2)):
            with self.subTest(args=args[1:3]), self.assertRaises(ValueError):
                tool._verify_rows(*args)

    def test_re_signed_noncanonical_row_still_fails(self):
        raw = b'[ 1 ]\n'
        packed = gzip.compress(raw, mtime=0)
        with self.assertRaises(ValueError):
            tool._verify_rows(base64.b64encode(packed).decode(), len(raw),
                hashlib.sha256(packed).hexdigest(), hashlib.sha256(raw).hexdigest(),
                1, 1)

    def test_highly_compressible_valid_row_crosses_bounded_inflate_blocks(self):
        value = fixture(["x" * (3 * 1024 * 1024)])
        self.assertEqual(tool._verify_rows(*value, 1, 1), 1)

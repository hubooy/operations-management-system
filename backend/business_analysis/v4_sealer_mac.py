"""Purpose-separated v4 segment MAC verification for a future protected sealer.

The caller supplies only the derived 32-byte verification key. This module has
no database, credential store, role activation, claim or seal side effect.
"""
import hashlib
import hmac
import re

from .contracts import AnalysisContractError, canonical


SEGMENT_PURPOSE = b"teruisi:business-v4:resumable-validation:v1\x00"
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX16 = re.compile(r"[0-9a-f]{16}\Z")


def derive_segment_key(master_secret):
    """Provisioning/test helper; a sealer runtime must not receive the master."""
    if type(master_secret) is not str or len(master_secret) < 32:
        raise AnalysisContractError("v4段派生主密钥无效")
    return hmac.new(master_secret.encode("utf-8"), SEGMENT_PURPOSE,
                    hashlib.sha256).digest()


def segment_key_id(derived_key):
    if type(derived_key) is not bytes or len(derived_key) != 32:
        raise AnalysisContractError("v4段派生验证密钥无效")
    return hashlib.sha256(derived_key).hexdigest()[:16]


def verify_segment(payload, mac, derived_key, expected_key_id):
    """Check 0036 HMAC bytes after the caller validated claim and payload shape."""
    actual_id = segment_key_id(derived_key)
    if (type(expected_key_id) is not str or HEX16.fullmatch(expected_key_id) is None
            or not hmac.compare_digest(actual_id, expected_key_id)
            or type(mac) is not str or HEX64.fullmatch(mac) is None
            or type(payload) is not dict):
        raise AnalysisContractError("v4段MAC输入、密钥版本或格式无效")
    try:
        body = canonical(payload).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise AnalysisContractError("v4段证明正文无法规范编码") from error
    if len(body) > 32_768:
        raise AnalysisContractError("v4段证明正文超过固定容量")
    expected = hmac.new(derived_key, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, mac):
        raise AnalysisContractError("v4段HMAC验证失败")
    return True

"""Purpose-separated application MAC for v4 internal parent seals."""
import hashlib
import hmac
import re

from django.conf import settings

from business_analysis import evidence_seal_v4

from . import business_v4_validation as validation
from .policy import AiError

PURPOSE = b"teruisi:business-v4:parent-seal:v1\x00"


def _key():
    secret = getattr(settings, "DJANGO_INTERNAL_SECRET", None)
    if type(secret) is not str or len(secret) < 32:
        raise AiError("v4封存应用签名密钥不可用", "service_unavailable", 503)
    key = hmac.new(secret.encode("utf-8"), PURPOSE, hashlib.sha256).digest()
    # 0038 pins the same master-key identity as the 0036 segment attempt;
    # the actual MAC key uses a distinct purpose and is never reused.
    return key, validation._key()[1]


def sign(body_json):
    evidence_seal_v4.read(body_json)
    key, key_id = _key()
    return {"keyId": key_id, "bodyMac": hmac.new(key,
        body_json.encode("utf-8"), hashlib.sha256).hexdigest()}


def verify(body_json, body_mac, key_id):
    evidence_seal_v4.read(body_json)
    key, current_id = _key()
    if (type(body_mac) is not str or re.fullmatch(r"[0-9a-f]{64}", body_mac) is None
            or key_id != current_id or not hmac.compare_digest(body_mac,
                hmac.new(key, body_json.encode("utf-8"), hashlib.sha256).hexdigest())):
        raise AiError("v4封存正文HMAC或密钥版本无效", "conflict", 409)
    return current_id

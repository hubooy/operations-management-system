"""Pure digest of the versioned current-state v10 download fence.

The protected preflight obtains the body from the owned SQL bridge and hashes
it before creating a 0057 attestation. This digest is not authorization by
itself; the reader bridge must recompute the same body from current rows.
"""
import json
import re

from .contracts import AnalysisContractError, canonical, digest


SCHEMA = "business-promotion-budget-v10-download-fence-v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_FIELDS = frozenset({"schemaVersion", "report", "workflow", "humanReview",
    "evidence", "budget", "file", "approvedContentDigest"})


def _bounded(value):
    try:
        raw = canonical(value)
        if len(raw.encode("utf-8")) > 32768:
            raise AnalysisContractError("预算下载栅栏正文超过固定容量")
        return json.loads(raw)
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise AnalysisContractError("预算下载栅栏正文不是有界JSON") from error


def checked(value):
    body = _bounded(value)
    if (type(body) is not dict or set(body) != _FIELDS
            or body["schemaVersion"] != SCHEMA
            or any(type(body[key]) is not dict for key in
                ("report", "workflow", "humanReview", "evidence", "file"))
            or body["budget"] is not None and type(body["budget"]) is not dict
            or type(body["approvedContentDigest"]) is not str
            or _SHA.fullmatch(body["approvedContentDigest"]) is None):
        raise AnalysisContractError("预算下载栅栏版本或字段无效")
    required = {"runId", "attempt", "bindingDigest", "compactSha256",
        "storedBytes", "attestationId", "fullManifestSha256",
        "fullManifestDigest", "budgetProofDigest"}
    file = body["file"]
    if (set(file) != required or type(file["attempt"]) is not int
            or not 1 <= file["attempt"] <= 5
            or type(file["storedBytes"]) is not int
            or not 1 <= file["storedBytes"] <= 1024*1024*1024
            or any(type(file[key]) is not str or _SHA.fullmatch(file[key]) is None
                for key in ("bindingDigest", "compactSha256", "attestationId",
                    "fullManifestSha256", "fullManifestDigest", "budgetProofDigest"))):
        raise AnalysisContractError("预算下载栅栏文件身份无效")
    return body


def fence_digest(value):
    return digest(checked(value))

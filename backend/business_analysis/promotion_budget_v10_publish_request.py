"""Pure, non-authoritative fingerprint for one v10 publish intent.

The protected verifier must independently check the current report, approval,
budget and staged bytes. A digest alone cannot authorize publication.
"""
import re

from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, digest


SCHEMA = "business-budget-v10-publish-request-v1"
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def body(*, run_id, attempt, expected_version, attestation_id,
         attestation_sha256, binding_digest, full_manifest_digest,
         full_manifest_sha256):
    if (type(run_id) is not str or _ID.fullmatch(run_id) is None
            or type(attempt) is not int or not 1 <= attempt <= 5
            or type(expected_version) is not int or
            not 1 <= expected_version <= MAX_SAFE_INTEGER
            or any(type(value) is not str or _SHA.fullmatch(value) is None
                for value in (attestation_id, attestation_sha256,
                    binding_digest, full_manifest_digest,
                    full_manifest_sha256))):
        raise AnalysisContractError("预算发布请求的身份、版本或摘要无效")
    return {"schemaVersion": SCHEMA, "runId": run_id,
        "attempt": attempt, "expectedVersion": expected_version,
        "attestationId": attestation_id,
        "attestationSha256": attestation_sha256,
        "bindingDigest": binding_digest,
        "fullManifestDigest": full_manifest_digest,
        "fullManifestSha256": full_manifest_sha256}


def request_digest(**kwargs):
    return digest(body(**kwargs))

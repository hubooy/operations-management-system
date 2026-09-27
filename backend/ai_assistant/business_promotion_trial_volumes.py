"""Unregistered approved renderer-9 promotion trial temporary files.

The complete manifest proves temporary bytes and table coverage. It is not a
persisted file-run receipt, download authorization, or DB ready transition.
"""
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from business_analysis import promotion_trial_table_schema, volume_delivery, volume_files, volume_plan
from business_analysis.contracts import AnalysisContractError
from business_analysis import promotion_action_tables
from . import business_promotion_approved_content as approved_content
from . import business_promotion_content_contract as content_contract
from . import business_promotion_file_proof as file_proof
from . import business_promotion_file_tables as file_tables
from . import business_promotion_formal_export as formal_pair
from .policy import AiError, canonical, current_principal, digest


SCHEMA = "business-promotion-trial-volumes-candidate-v1"
MAX_TOTAL_BYTES = volume_delivery.MAX_DELIVERY_BYTES


def _conflict(message="词货多卷候选与当前已批准报告不一致"):
    raise AiError(message, "conflict", 409)


def _policy(max_tables, max_rows, max_volumes):
    for value, maximum in ((max_tables, 120), (max_rows, 1_000_000), (max_volumes, 100)):
        if type(value) is not int or not 1 <= value <= maximum:
            raise AiError("多卷容量只能在原边界内收紧", "invalid_request", 400)
    return {"max_tables": max_tables, "max_rows": max_rows, "max_volumes": max_volumes}


def _contract(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except AnalysisContractError as error:
        code = "payload_too_large" if any(word in str(error) for word in
            ("容量", "超过", "超限")) else "conflict"
        raise AiError("词货多卷计划、文件或清单未通过固定边界", code,
            413 if code == "payload_too_large" else 409) from error


class _Budget:
    def __init__(self, limit):
        self.limit, self.used = limit, 0

    def stream(self, raw):
        return _Output(raw, self)


class _Output:
    def __init__(self, raw, budget):
        self.raw, self.budget, self.high_water = raw, budget, 0

    def write(self, data):
        high = max(self.high_water, self.raw.tell() + len(data))
        growth = high - self.high_water
        if self.budget.used + growth > self.budget.limit:
            raise AiError("多卷临时输出总量超过1GiB边界", "payload_too_large", 413)
        written = self.raw.write(data)
        if written != len(data): _conflict("多卷临时文件写入不完整")
        self.budget.used += growth
        self.high_water = high
        return written

    def __getattr__(self, key):
        return getattr(self.raw, key)


def _file(path, maximum):
    size, sha = 0, hashlib.sha256()
    with path.open("rb") as stream:
        while part := stream.read(volume_delivery.CHUNK_BYTES):
            size += len(part)
            if size > maximum:
                raise AiError("多卷临时文件超出单文件容量", "payload_too_large", 413)
            sha.update(part)
    if size == 0: _conflict("多卷临时文件为空")
    return {"bytes": size, "sha256": sha.hexdigest()}


@dataclass(frozen=True, slots=True)
class PreparedVolumes:
    _manifest: str
    _paths: dict
    _active: list

    @property
    def manifest(self):
        if not self._active[0]: _conflict("多卷临时文件已关闭")
        return json.loads(self._manifest)

    def path(self, volume_index, kind):
        if not self._active[0] or (volume_index, kind) not in self._paths:
            _conflict("多卷临时文件不存在或已关闭")
        return self._paths[volume_index, kind]


@contextmanager
def open_volumes(report_id, principal, *, checkpoint=None, material_limits=None,
                 max_tables=120, max_rows=1_000_000, max_volumes=100):
    """Yield all temporary pairs plus JSON only after complete source checks."""
    policy = _policy(max_tables, max_rows, max_volumes)
    current_principal(principal, admin=True)
    with TemporaryDirectory(prefix="teruisi-promotion-volumes-") as folder:
        directory, active = Path(folder), [True]
        paths = {}
        try:
            with file_tables.open_tables(report_id, principal, draft=False,
                    checkpoint=checkpoint, limits=material_limits) as (metadata, promotion):
                fixed, approved = metadata.value, metadata.approved_content
                if type(approved) is not content_contract.PreparedContent:
                    _conflict("多卷候选缺少已批准五角色内容")
                value = approved.value
                material = fixed["sourceSummary"]["sourceMaterials"]
                proof = file_proof.prepare(approved, material).value
                if (proof["materialManifestDigest"] != fixed["materialManifestDigest"]
                        or proof["contentDtoDigest"] != fixed["reportBinding"]["approvedContentDigest"]):
                    _conflict("多卷词货证明与报告来源不同")
                with formal_pair._source_tables(report_id, principal,
                        fixed["reportBinding"], checkpoint, trial=True) as (sealed, source):
                    base = formal_pair._tables(value, promotion)
                    action = promotion_action_tables.project(value)
                    tables = (*base[:-2], action, *sealed, *base[-2:])
                    request = _contract(volume_files.request_for, tables, report_id=report_id,
                        evidence_digest=value["binding"]["sealedDigest"], renderer_version=9)
                    plan = _contract(volume_plan.build, request, **policy)
                    action_sha = hashlib.sha256()
                    for row in action.rows:
                        action_sha.update((canonical(list(row))+"\n").encode("utf-8"))
                    trial_proof = {"schemaVersion": "business-promotion-trial-file-proof-v2",
                        "rendererVersion": 9, "reportId": report_id,
                        "contentDtoDigest": value["dtoDigest"],
                        "humanReviewDigest": proof["humanReviewDigest"],
                        "promotionFileProofDigest": proof["proofDigest"],
                        "sealedSourcesDigest": source["sourcesDigest"],
                        "sourceDescriptorDigest": plan["sourceDescriptorDigest"],
                        "tableSchemaDigest": promotion_trial_table_schema.digest_tables(tables),
                        "actionTableKey": action.key, "actionRowCount": action.row_count,
                        "actionRowDigest": action_sha.hexdigest(),
                        "scopeTableKeys": ["promotion-trial-source-scope", "promotion-trial-boundaries"],
                        "promotionTableKeys": [table.key for table in promotion],
                        "budgetDelivered": False}
                    trial_proof["proofDigest"] = digest(trial_proof)
                    file_metadata = {"schemaVersion": SCHEMA, "reportId": report_id,
                        "rendererVersion": 9, "promotionFileProof": proof,
                        "promotionTrialProof": trial_proof,
                        "sealedSourceCount": source["sourceCount"],
                        "sealedSourcesDigest": source["sourcesDigest"],
                        "sealedSourceTableCount": source["sourceTableCount"],
                        "limitations": list(content_contract.LIMITATIONS),
                        "deliveryAuthorized": False, "registeredRenderer": False}
                    file_metadata["metadataDigest"] = digest(file_metadata)
                    budget = _Budget(MAX_TOTAL_BYTES-volume_delivery.MAX_MANIFEST_BYTES)
                    with ExitStack() as stack:
                        outputs = []
                        for index in range(1, plan["volumeCount"] + 1):
                            streams = {}
                            for kind in ("html", "xlsx"):
                                path = directory / f"volume-{index:03}.{kind}"
                                paths[index, kind] = path
                                streams[kind] = budget.stream(stack.enter_context(path.open("w+b")))
                            outputs.append(volume_files.VolumeStreams(**streams))
                        full = _contract(volume_files.render, tables, outputs, report_id=report_id,
                            evidence_digest=value["binding"]["sealedDigest"],
                            renderer_version=9, plan=plan,
                            title="推广专项试用分析 · " + report_id,
                            metadata=file_metadata, checkpoint=checkpoint, **policy)
                    if full.get("promotionFileProof") != proof:
                        _conflict("完整多卷清单缺少固定词货证明")
                    if full.get("promotionTrialProof") != trial_proof:
                        _conflict("完整多卷清单缺少试用来源与行动证明")
                    if ([entry["key"] for entry in full["tables"][-2:]] !=
                            [table.key for table in promotion]):
                        _conflict("词货双表未位于完整多卷清单末尾")
                    candidate_binding = digest([SCHEMA, report_id, proof["proofDigest"],
                        trial_proof["proofDigest"], source["sourcesDigest"], "trial"])
                    compact, raw = _contract(volume_delivery.make, full,
                        binding_digest=candidate_binding, attempt=1, draft=False,
                        renderer_version=9, **policy)
                    checked = _contract(volume_delivery.verify_full, compact, raw,
                        binding_digest=candidate_binding, attempt=1, draft=False,
                        report_id=report_id,
                        evidence_digest=value["binding"]["sealedDigest"],
                        renderer_version=9, **policy)
                    if checked != full: _conflict("多卷完整清单校验未回到同一报告")
            # Both source contexts have exited normally and rechecked roots.
            if approved_content.build(report_id, principal)["dtoDigest"] != value["dtoDigest"]:
                _conflict("多卷完成后人审或五角色内容发生变化")
            paths[0, "json"] = directory / "manifest.json"
            paths[0, "json"].write_bytes(raw)
            descriptors = [*compact["files"], compact["manifestFile"]]
            files = []
            for item in descriptors:
                key = item["volumeIndex"], item["format"]
                actual = _file(paths[key], volume_delivery.MAX_MANIFEST_BYTES if key[0] == 0
                    else volume_delivery.MAX_FILE_BYTES)
                if actual != {name: item[name] for name in ("bytes", "sha256")}:
                    _conflict("多卷实际文件与完整清单摘要不同")
                files.append({"volumeIndex": key[0], "format": key[1], **actual})
            if sum(item["bytes"] for item in files) > MAX_TOTAL_BYTES:
                raise AiError("多卷临时输出超过1GiB容量", "payload_too_large", 413)
            receipt = {"schemaVersion": SCHEMA, "reportId": report_id,
                "rendererVersion": 9, "compactManifest": compact,
                "fullManifestDigest": full["manifestDigest"],
                "promotionFileProof": proof,
                "promotionTrialProof": trial_proof,
                "sealedSourceCount": source["sourceCount"],
                "sealedSourcesDigest": source["sourcesDigest"],
                "sealedSourceTableCount": source["sourceTableCount"],
                "sourceTableCount": full["sourceTableCount"],
                "volumeCount": plan["volumeCount"], "files": files,
                "deliveryAuthorized": False, "registeredRenderer": False,
                "authorityVerified": False}
            receipt["receiptDigest"] = digest(receipt)
            prepared = PreparedVolumes(canonical(receipt), paths, active)
            yield prepared
            for item in descriptors:
                key = item["volumeIndex"], item["format"]
                if _file(paths[key], volume_delivery.MAX_MANIFEST_BYTES if key[0] == 0
                        else volume_delivery.MAX_FILE_BYTES) != {name: item[name]
                            for name in ("bytes", "sha256")}:
                    _conflict("交接期间多卷临时文件发生变化")
            if approved_content.build(report_id, principal)["dtoDigest"] != value["dtoDigest"]:
                _conflict("交接期间人审或五角色内容发生变化")
            current_principal(principal, admin=True)
        except (AnalysisContractError, file_proof.FileProofError) as error:
            code = "payload_too_large" if any(word in str(error) for word in
                ("容量", "超过", "超限")) else "conflict"
            raise AiError("词货完整多卷候选未通过文件或容量核验", code,
                413 if code == "payload_too_large" else 409) from error
        finally:
            active[0] = False

"""Unregistered renderer-10 promotion budget temporary multi-volume candidate.

Every byte is temporary and bound to the actual approved report, sealed Reader
and current fixed-budget row (if any). This module has no file-run mutation,
route, model dispatch or publication authority.
"""
from contextlib import ExitStack, contextmanager
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from business_analysis import (html_slim_payload_v11, promotion_action_tables, promotion_budget_v10,
    promotion_trial_scope, promotion_trial_table_schema, volume_delivery,
    volume_files, volume_plan)
from business_analysis.contracts import AnalysisContractError
from business_analysis.report_files import Table
from . import business_promotion_approved_content as approved_content
from . import business_promotion_budget as budget_reader
from . import business_promotion_content_contract as content_contract
from . import business_promotion_file_proof as file_proof
from . import business_promotion_file_tables as file_tables
from . import business_promotion_formal_export as formal_pair
from . import business_promotion_trial_volumes as trial
from .policy import AiError, canonical, current_principal, digest


SCHEMA = "business-promotion-budget-v10-volumes-candidate-v1"
SLIM_SCHEMA = "business-promotion-budget-v11-slim-volumes-candidate-v1"
MAX_TOTAL_BYTES = volume_delivery.MAX_DELIVERY_BYTES


def _conflict(message="推广预算临时文件与当前已批准来源不一致"):
    raise AiError(message, "conflict", 409)


def _budget_material(report_id, principal, fixed, material_report):
    """Fresh owning read; a claimed budget may never become an empty model."""
    if material_report.get("budgetRef") is None:
        return None, None
    owned = budget_reader._roots(report_id, principal)
    # file_tables._bound appends these two reviewed-content fences to the
    # runtime.bound_persisted base returned by budget_reader._roots. Compare
    # the entire shared base, and require exactly those two known extensions;
    # file_tables and approved_content independently verify their values.
    extensions = {"reviewDigest", "approvedContentDigest"}
    base = owned["bound"]
    if (type(base) is not dict or type(fixed) is not dict
            or set(fixed) != set(base) | extensions
            or any(type(fixed[key]) is not str or len(fixed[key]) != 64
                for key in extensions)
            or canonical({key: fixed[key] for key in base}) != canonical(base)):
        _conflict("预算与当前完整词货报告根不一致")
    prepared = owned["prepared"]
    if prepared.reference != material_report["budgetRef"]:
        _conflict("预算引用与已批准来源材料不一致")
    data = {"binding": prepared.binding, "reference": prepared.reference,
        "result": prepared.result}
    fingerprint = digest([owned["rowDigest"], prepared.binding_json,
        prepared.result_json])
    return data, fingerprint


def _fresh(report_id, principal, fixed, approved_digest,
           material_report, budget_fingerprint):
    latest, _ = file_tables._bound(report_id, principal, False)
    if canonical(latest) != canonical(fixed):
        _conflict("多卷期间报告或人审已变化")
    if approved_content.build(report_id, principal)["dtoDigest"] != approved_digest:
        _conflict("多卷期间已批准五角色内容已变化")
    _, current = _budget_material(report_id, principal, fixed, material_report)
    if current != budget_fingerprint:
        _conflict("多卷期间固定预算参数或封存重算已变化")
    current_principal(principal, admin=True)


def _updated_boundary(sealed, *, budget_present):
    if not budget_present:
        return tuple(sealed)
    rewritten = []
    old = list(promotion_trial_scope.BOUNDARIES[4])
    new = ["可编辑预算工作表", "试算候选",
        "本版含同报告固定预算的离线可编辑试算；初值与公式待独立复核，不修改原报告或自动执行投放。"]
    for table in sealed:
        if table.key != "promotion-trial-boundaries":
            rewritten.append(table)
            continue
        if (type(table) is not Table or table.row_count != len(promotion_trial_scope.BOUNDARIES)
                or type(table.rows) is not list or list(table.rows[4]) != old):
            _conflict("旧版预算边界表已变化，不能改写为新版试算说明")
        rows = [list(row) for row in table.rows]
        rows[4] = new
        rewritten.append(Table(table.key, table.title,
            "本版能力和口径边界；预算页是未复核本地试算，不代表执行或真实利润。",
            table.columns, rows, table.row_count))
    if sum(table.key == "promotion-trial-boundaries" for table in rewritten) != 1:
        _conflict("新版来源边界表缺失或重复")
    return tuple(rewritten)


def _trial_proof(report_id, value, proof, source, tables, action,
                 promotion, policy):
    request = trial._contract(volume_files.request_for, tables,
        report_id=report_id, evidence_digest=value["binding"]["sealedDigest"],
        renderer_version=9)
    plan = trial._contract(volume_plan.build, request, **policy)
    sha = hashlib.sha256()
    for row in action.rows:
        sha.update((canonical(list(row)) + "\n").encode("utf-8"))
    result = {"schemaVersion": "business-promotion-trial-file-proof-v2",
        "rendererVersion": 9, "reportId": report_id,
        "contentDtoDigest": value["dtoDigest"],
        "humanReviewDigest": proof["humanReviewDigest"],
        "promotionFileProofDigest": proof["proofDigest"],
        "sealedSourcesDigest": source["sourcesDigest"],
        "sourceDescriptorDigest": plan["sourceDescriptorDigest"],
        "tableSchemaDigest": promotion_trial_table_schema.digest_tables(tables),
        "actionTableKey": action.key, "actionRowCount": action.row_count,
        "actionRowDigest": sha.hexdigest(),
        "scopeTableKeys": ["promotion-trial-source-scope", "promotion-trial-boundaries"],
        "promotionTableKeys": [table.key for table in promotion],
        "budgetDelivered": False}
    result["proofDigest"] = digest(result)
    descriptors = [dict(item) for item in request["tables"]]
    for item in descriptors:
        if item["key"] == action.key:
            item["rowDigest"] = result["actionRowDigest"]
    trial._contract(volume_delivery.trial_proof, result,
        {"reportId": report_id, "sourceDescriptorDigest":
            plan["sourceDescriptorDigest"], "tableSchemaDigest":
            result["tableSchemaDigest"], "promotionFileProof": proof,
            "tables": descriptors,
            "volumes": [{"nativeBudgetSheets": 0,
                "offlineBudgetEnabled": False} for _ in plan["volumes"]]})
    return result


@contextmanager
def _open_versioned(report_id, principal, *, renderer_version,
                    checkpoint=None, material_limits=None,
                    max_tables=120, max_rows=1_000_000, max_volumes=100):
    """Yield temporary v10/v11 pairs from the same current approved roots."""
    if type(renderer_version) is not int or renderer_version not in (10, 11):
        _conflict("预算临时渲染版本无效")
    schema = SCHEMA if renderer_version == 10 else SLIM_SCHEMA
    policy = trial._policy(max_tables, max_rows, max_volumes)
    current_principal(principal, admin=True)
    with TemporaryDirectory(prefix="teruisi-promotion-budget-v10-") as folder:
        directory, active, paths = Path(folder), [True], {}
        try:
            with file_tables.open_tables(report_id, principal, draft=False,
                    checkpoint=checkpoint, limits=material_limits) as (metadata, promotion):
                fixed, approved = metadata.value, metadata.approved_content
                if type(approved) is not content_contract.PreparedContent:
                    _conflict("预算文件缺少真实批准的五角色内容")
                value = approved.value
                material = fixed["sourceSummary"]["sourceMaterials"]
                proof = file_proof.prepare(approved, material).value
                if (proof["materialManifestDigest"] != fixed["materialManifestDigest"]
                        or proof["contentDtoDigest"] !=
                        fixed["reportBinding"]["approvedContentDigest"]):
                    _conflict("预算文件词货证明与报告来源不同")
                report_binding = material["reportBinding"]
                budget_material, budget_fingerprint = _budget_material(
                    report_id, principal, fixed["reportBinding"], report_binding)
                with formal_pair._source_tables(report_id, principal,
                        fixed["reportBinding"], checkpoint, trial=True) as (sealed, source):
                    base = formal_pair._tables(value, promotion)
                    action = promotion_action_tables.project(value)
                    original = (*base[:-2], action, *sealed, *base[-2:])
                    lineage = _trial_proof(report_id, value, proof, source,
                        original, action, promotion, policy)
                    budget = trial._contract(promotion_budget_v10.project,
                        trial_proof=lineage, approved_binding=value["binding"],
                        report_binding=report_binding,
                        approved_dto_digest=value["dtoDigest"],
                        budget_material=budget_material)
                    adjusted = _updated_boundary(sealed,
                        budget_present=budget.offline_budget is not None)
                    tables = (*base[:-2], action, *adjusted, *budget.tables,
                        *base[-2:])
                    request = trial._contract(volume_files.request_for, tables,
                        report_id=report_id,
                        evidence_digest=value["binding"]["sealedDigest"],
                        renderer_version=renderer_version)
                    plan = trial._contract(volume_plan.build, request,
                        native_budget_sheets=budget.proof["nativeBudgetSheets"],
                        **policy)
                    if plan["volumes"][0]["kind"] == "budget_only":
                        _conflict("首卷须同时容纳至少一张来源表")
                    schema_digest = promotion_trial_table_schema.digest_tables(tables)
                    file_metadata = {"schemaVersion": schema, "reportId": report_id,
                        "rendererVersion": renderer_version, "promotionFileProof": proof,
                        "promotionTrialProof": lineage,
                        "promotionBudgetProof": budget.proof,
                        "tableSchemaDigest": schema_digest,
                        "sealedSourceCount": source["sourceCount"],
                        "sealedSourcesDigest": source["sourcesDigest"],
                        "sealedSourceTableCount": source["sourceTableCount"],
                        "limitations": list(content_contract.LIMITATIONS),
                        "deliveryAuthorized": False, "registeredRenderer": False}
                    file_metadata["metadataDigest"] = digest(file_metadata)
                    output_budget = trial._Budget(MAX_TOTAL_BYTES-
                        volume_delivery.MAX_MANIFEST_BYTES)
                    with ExitStack() as stack:
                        outputs = []
                        for index in range(1, plan["volumeCount"] + 1):
                            streams = {}
                            for kind in ("html", "xlsx"):
                                path = directory / f"volume-{index:03}.{kind}"
                                paths[index, kind] = path
                                streams[kind] = output_budget.stream(stack.enter_context(
                                    path.open("w+b")))
                            outputs.append(volume_files.VolumeStreams(**streams))
                        full = trial._contract(volume_files.render, tables, outputs,
                            report_id=report_id,
                            evidence_digest=value["binding"]["sealedDigest"],
                            renderer_version=renderer_version, plan=plan,
                            title="推广专项预算试用分析 · " + report_id,
                            metadata=file_metadata, checkpoint=checkpoint,
                            offline_budget=budget.offline_budget,
                            excel_budget=budget.excel_budget, **policy)
                    if (full.get("promotionFileProof") != proof or
                            full.get("promotionTrialProof") != lineage or
                            full.get("promotionBudgetProof") != budget.proof or
                            full.get("tableSchemaDigest") != schema_digest):
                        _conflict("版本10完整清单缺少固定词货、来源或预算证明")
                    if renderer_version == 11 and (
                            full.get("promotionSlimProof", {}).get("htmlPayloadVersion") != 2 or
                            full["promotionSlimProof"]["sourceBudgetProofDigest"] != budget.proof["proofDigest"]):
                        _conflict("版本11完整清单缺少压缩行及预算证明")
                    by_key = {item["key"]: item for item in full["tables"]}
                    for key, rows, sha in zip(budget.proof["tableKeys"],
                            budget.proof["tableRowCounts"],
                            budget.proof["tableRowDigests"]):
                        if by_key[key]["rowCount"] != rows or by_key[key]["rowDigest"] != sha:
                            _conflict("预算表实际行与源绑定证明不一致")
                    if ([item["key"] for item in full["tables"][-2:]] !=
                            [table.key for table in promotion]):
                        _conflict("词货原双表未位于完整清单末尾")
                    binding_parts = [schema, report_id,
                        proof["proofDigest"], lineage["proofDigest"],
                        budget.proof["proofDigest"], schema_digest,
                        source["sourcesDigest"], "temporary"]
                    if renderer_version == 11:
                        binding_parts.append(full["promotionSlimProof"]["proofDigest"])
                    candidate_binding = digest(binding_parts)
                    compact, raw = trial._contract(volume_delivery.make, full,
                        binding_digest=candidate_binding, attempt=1, draft=False,
                        renderer_version=renderer_version, **policy)
                    checked = trial._contract(volume_delivery.verify_full,
                        compact, raw, binding_digest=candidate_binding,
                        attempt=1, draft=False, report_id=report_id,
                        evidence_digest=value["binding"]["sealedDigest"],
                        renderer_version=renderer_version, **policy)
                    if checked != full:
                        _conflict("版本10完整清单未回到同一报告")
            _fresh(report_id, principal, fixed["reportBinding"],
                value["dtoDigest"], report_binding, budget_fingerprint)
            paths[0, "json"] = directory / "manifest.json"
            paths[0, "json"].write_bytes(raw)
            descriptors = [*compact["files"], compact["manifestFile"]]
            files = []
            for item in descriptors:
                key = item["volumeIndex"], item["format"]
                actual = trial._file(paths[key],
                    volume_delivery.MAX_MANIFEST_BYTES if key[0] == 0
                    else volume_delivery.MAX_FILE_BYTES)
                if actual != {name: item[name] for name in ("bytes", "sha256")}:
                    _conflict("版本10实际文件字节与完整清单不同")
                files.append({"volumeIndex": key[0], "format": key[1], **actual})
            if sum(item["bytes"] for item in files) > MAX_TOTAL_BYTES:
                raise AiError("版本10临时输出超过1GiB容量", "payload_too_large", 413)
            if renderer_version == 11:
                for volume in full["volumes"]:
                    html_slim_payload_v11.verify_file(
                        paths[volume["volumeIndex"], "html"], volume,
                        checkpoint=checkpoint)
            receipt = {"schemaVersion": schema, "reportId": report_id,
                "rendererVersion": renderer_version, "compactManifest": compact,
                "fullManifestDigest": full["manifestDigest"],
                "promotionFileProof": proof, "promotionTrialProof": lineage,
                "promotionBudgetProof": budget.proof,
                "tableSchemaDigest": schema_digest,
                "sealedSourceCount": source["sourceCount"],
                "sealedSourcesDigest": source["sourcesDigest"],
                "sealedSourceTableCount": source["sourceTableCount"],
                "sourceTableCount": full["sourceTableCount"],
                "volumeCount": plan["volumeCount"], "files": files,
                "deliveryAuthorized": False, "registeredRenderer": False,
                "authorityVerified": False}
            if renderer_version == 11:
                receipt["promotionSlimProof"] = full["promotionSlimProof"]
                receipt["htmlPayloadVersion"] = 2
            receipt["receiptDigest"] = digest(receipt)
            prepared = trial.PreparedVolumes(canonical(receipt), paths, active)
            yield prepared
            for item in descriptors:
                key = item["volumeIndex"], item["format"]
                if trial._file(paths[key],
                        volume_delivery.MAX_MANIFEST_BYTES if key[0] == 0
                        else volume_delivery.MAX_FILE_BYTES) != {name: item[name]
                            for name in ("bytes", "sha256")}:
                    _conflict("交接期间版本10临时文件已变化")
            _fresh(report_id, principal, fixed["reportBinding"],
                value["dtoDigest"], report_binding, budget_fingerprint)
        except (AnalysisContractError, file_proof.FileProofError) as error:
            code = "payload_too_large" if any(word in str(error) for word in
                ("容量", "超过", "超限")) else "conflict"
            raise AiError("推广预算临时多卷候选未通过来源或容量核验",
                code, 413 if code == "payload_too_large" else 409) from error
        finally:
            active[0] = False


@contextmanager
def open_volumes(report_id, principal, *, checkpoint=None, material_limits=None,
                 max_tables=120, max_rows=1_000_000, max_volumes=100):
    """Preserved renderer-10 temporary contract and bytes."""
    with _open_versioned(report_id, principal, renderer_version=10,
            checkpoint=checkpoint, material_limits=material_limits,
            max_tables=max_tables, max_rows=max_rows,
            max_volumes=max_volumes) as prepared:
        yield prepared

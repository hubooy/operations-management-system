"""Candidate-only paired volume renderer; no production dispatch or publication.

The caller owns empty, readable, writable, seekable temporary streams. A returned
complete manifest is the only delivery receipt. If any operation raises, ALL
streams are unpublished partial output, including earlier valid ZIP files. This
module never lists source rows, rewinds a source, or retries a failed rendering.
Byte overflow rejects the entire delivery; it does not dynamically split bytes.
"""
from dataclasses import dataclass
import hashlib
import math
import os

from . import report_files, volume_plan
from .contracts import AnalysisContractError, canonical, digest
from .report_files import Column, Table

MAX_CONTEXT_BYTES = 2 * 1024 * 1024


def _snapshot_json(value, name):
    """Copy bounded passive JSON before a callback or source can mutate aliases."""
    remaining, nodes = MAX_CONTEXT_BYTES, 100000
    def charge(size):
        nonlocal remaining
        remaining -= size
        if remaining < 0:
            raise AnalysisContractError(name+"超过2MiB规范JSON容量")
    def visit(item, depth):
        nonlocal nodes
        nodes -= 1
        if depth > 32 or nodes < 0:
            raise AnalysisContractError(name+"嵌套或节点超过容量")
        kind = type(item)
        if item is None or kind in (str, bool, int, float):
            if kind is str and len(item) > remaining or kind is int and abs(item) >= 2**256 or kind is float and not math.isfinite(item):
                raise AnalysisContractError(name+"标量超过规范JSON容量")
            try:
                charge(len(canonical(item).encode()))
            except (ValueError, UnicodeError) as error:
                raise AnalysisContractError(name+"包含无效JSON文本") from error
            return item
        if kind is dict:
            if len(item) > nodes:
                raise AnalysisContractError(name+"对象超过节点容量")
            charge(2)
            copied = {}
            for key, child in item.items():
                if type(key) is not str:
                    raise AnalysisContractError(name+"对象键必须为文本")
                charge(2)
                copied[visit(key, depth+1)] = visit(child, depth+1)
            return copied
        if kind in (list, tuple):
            if len(item) > nodes:
                raise AnalysisContractError(name+"数组超过节点容量")
            charge(2+len(item))
            return [visit(child, depth+1) for child in item]
        raise AnalysisContractError(name+"只能包含被动JSON值")
    return visit(value, 0)


def _budget_snapshot(value, report_id, native_enabled):
    from .budget import calculate
    from .budget_offline import payload
    copied = _snapshot_json(value, "预算上下文")
    required = {"schemaVersion", "reportId", "planDigest", "plan", "baselines", "expected"}
    if type(copied) is not dict or not required <= set(copied) or set(copied)-required-{"excelEnabled"} or copied.get("reportId") != report_id:
        raise AnalysisContractError("预算试算与多卷报告身份或结构不一致")
    if "excelEnabled" in copied and (type(copied["excelEnabled"]) is not bool or copied["excelEnabled"] and not native_enabled):
        raise AnalysisContractError("预算声明的Excel可编辑状态与实际交付不一致")
    try:
        expected = payload(calculate(copied["plan"], copied["baselines"]), report_id)
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, ZeroDivisionError) as error:
        raise AnalysisContractError("预算输入无法按固定证据复算") from error
    if canonical({key: copied[key] for key in required}) != canonical(expected):
        raise AnalysisContractError("预算参数摘要或离线初值与固定证据不一致")
    return {**expected, "excelEnabled": native_enabled}


@dataclass(frozen=True)
class VolumeStreams:
    xlsx: object
    html: object


def request_for(tables, *, report_id, evidence_digest, renderer_version):
    """Build descriptors without obtaining a source iterator or reading a row."""
    if not isinstance(tables, (list, tuple)) or len(tables) > 12000 or any(not isinstance(t, Table) for t in tables):
        raise AnalysisContractError("多卷输入须为最多12000项的完整Table列表")
    return {"schemaVersion": volume_plan.REQUEST_SCHEMA, "reportId": report_id,
            "evidenceDigest": evidence_digest, "rendererVersion": renderer_version,
            "tables": [{"key": t.key, "title": t.title, "rowCount": t.row_count,
                        "columnCount": len(t.columns)} for t in tables]}


class _Source:
    def __init__(self, table):
        self.table, self.iterator = table, None
        self.position, self.finished, self.digest = 0, False, hashlib.sha256()

    def fragment(self, descriptor):
        fragment_digest = hashlib.sha256()

        def rows():
            if self.finished or self.position != descriptor["rowOffset"]:
                raise AnalysisContractError("分片源游标顺序不一致，禁止重读或跳行")
            if self.iterator is None:
                self.iterator = iter(self.table.rows)
            for _ in range(descriptor["rowLimit"]):
                try:
                    row = next(self.iterator)
                except StopIteration as error:
                    raise AnalysisContractError("完整来源迟到缺行，禁止交付全部卷") from error
                if not isinstance(row, (list, tuple)) or len(row) != len(self.table.columns):
                    raise AnalysisContractError("分片源行宽无效")
                # Match the existing paired writer's explicit long-integer text
                # preservation. Only one bounded row is copied, never a table.
                values = list(row)
                try:
                    encoded = (canonical([str(v) if type(v) is int and abs(v) >= 10**15 else v for v in values])+"\n").encode()
                except (TypeError, ValueError, UnicodeError) as error:
                    raise AnalysisContractError("分片源行不能规范序列化") from error
                fragment_digest.update(encoded)
                self.digest.update(encoded)
                self.position += 1
                yield values
            if descriptor["fragmentIndex"] == descriptor["fragmentCount"]:
                try:
                    next(self.iterator)
                except StopIteration:
                    self.finished = True
                else:
                    raise AnalysisContractError("完整来源迟到多行，禁止交付全部卷")

        return rows(), fragment_digest


class _LimitedStream:
    def __init__(self, stream, maximum):
        self.stream, self.maximum = stream, maximum

    def write(self, value):
        if self.stream.tell()+len(value) > self.maximum:
            raise AnalysisContractError("多卷文件实际字节超限，整次交付拒绝；尚不支持按字节动态拆分")
        count = self.stream.write(value)
        if count != len(value):
            raise OSError("Temporary volume stream did not write all bytes")
        return count

    def __getattr__(self, name):
        return getattr(self.stream, name)


def _streams(outputs, count):
    if not isinstance(outputs, (list, tuple)) or len(outputs) != count or any(type(p) is not VolumeStreams for p in outputs):
        raise AnalysisContractError("调用者临时输出流数量须与完整卷数一致")
    identities, objects = set(), set()
    for pair in outputs:
        for stream in (pair.xlsx, pair.html):
            if id(stream) in objects:
                raise AnalysisContractError("不同文件不能共用同一个临时流")
            objects.add(id(stream))
            if not stream.readable() or not stream.writable() or not stream.seekable():
                raise AnalysisContractError("临时输出须可读、可写、可定位")
            if stream.tell() != 0 or stream.seek(0, 2) != 0:
                raise AnalysisContractError("临时输出必须为空且从零开始，禁止覆盖已有文件")
            stream.seek(0)
            try:
                stat = os.fstat(stream.fileno())
            except (AttributeError, OSError):
                continue
            identity = (stat.st_dev, stat.st_ino)
            if identity in identities:
                raise AnalysisContractError("多个临时流指向同一文件")
            identities.add(identity)


def _file_proof(stream, maximum, checkpoint):
    stream.flush()
    stream.seek(0)
    size, sha = 0, hashlib.sha256()
    while True:
        block = stream.read(65536)
        if not block:
            break
        size += len(block)
        if size > maximum:
            raise AnalysisContractError("最终文件字节超限，禁止交付全部卷")
        sha.update(block)
        if checkpoint and size % (4*1024*1024) == 0:
            checkpoint({"stage": "verifying_volume_file", "bytes": size})
    stream.seek(0)
    return {"bytes": size, "sha256": sha.hexdigest()}


def render(tables, outputs, *, report_id, evidence_digest, renderer_version, plan, title, metadata,
           max_tables=120, max_rows=1_000_000, max_volumes=100,
           max_file_bytes=report_files.MAX_FILE_BYTES, offline_budget=None, excel_budget=None, checkpoint=None,
           html_slim_v10=False):
    """Write all temporary pairs once and return a fully bound complete manifest.

Capacity options are trusted caller policy, not copied from the supplied plan.
max_file_bytes may only tighten the existing 256 MiB per-file bound for tests or
future deployment policy. Output streams remain caller-owned and are rewound on
success. No source iterator is consumed for rejected plans or budget-only input.
"""
    request = request_for(tables, report_id=report_id, evidence_digest=evidence_digest, renderer_version=renderer_version)
    native_budget_sheets = 3 if excel_budget is not None else 0
    policy = {"max_tables": max_tables, "max_rows": max_rows, "max_volumes": max_volumes, "native_budget_sheets": native_budget_sheets}
    volume_plan.verify(plan, request, **policy)
    if type(html_slim_v10) is not bool or html_slim_v10 and renderer_version != 10:
        raise AnalysisContractError("压缩HTML仅允许renderer 10显式候选，旧版本字节不可改")
    slim_html = html_slim_v10 or renderer_version == 11
    # Use the independently rebuilt bounded plan, not caller-owned mutable lists.
    planned = volume_plan.build(request, **policy)
    if any(v["kind"] == "budget_only" for v in planned["volumes"]):
        raise AnalysisContractError("现有双文件writer不支持budget_only卷；必须为首卷保留至少一个数据表片段")
    if type(max_file_bytes) is not int or not 1 <= max_file_bytes <= report_files.MAX_FILE_BYTES:
        raise AnalysisContractError("实际文件字节上限须为1—256MiB")
    report_files.text(title)
    if type(metadata) is not dict or "volumeDelivery" in metadata:
        raise AnalysisContractError("报告元信息须为对象，volumeDelivery为保留绑定字段")
    metadata = _snapshot_json(metadata, "报告元信息")
    if renderer_version == 9 and (offline_budget is not None or excel_budget is not None or
            type(metadata.get("promotionFileProof")) is not dict or
            type(metadata.get("promotionTrialProof")) is not dict):
        raise AnalysisContractError("renderer 9 试用报告须有双重证明且不得传入预算")
    if renderer_version in (10, 11) and (type(metadata.get("promotionFileProof")) is not dict or
            type(metadata.get("promotionTrialProof")) is not dict or
            type(metadata.get("promotionBudgetProof")) is not dict or
            type(metadata.get("tableSchemaDigest")) is not str or
            (offline_budget is None) != (excel_budget is None)):
        raise AnalysisContractError("renderer 10 预算候选缺少完整证明或双文件预算不一致")
    offline_budget = _budget_snapshot(offline_budget, report_id, native_budget_sheets == 3) if offline_budget is not None else None
    excel_budget = _budget_snapshot(excel_budget, report_id, True) if excel_budget is not None else None
    if offline_budget is not None and excel_budget is not None and canonical(offline_budget) != canonical(excel_budget):
        raise AnalysisContractError("HTML与Excel预算证据或参数不一致")
    if renderer_version in (10, 11):
        candidate = metadata["promotionBudgetProof"]
        if (candidate.get("offlinePayloadDigest") !=
                (digest(offline_budget) if offline_budget is not None else None)):
            raise AnalysisContractError("renderer 10 预算试算输入与候选证明不一致")
    # Freeze file references, table descriptors and column declarations before
    # even invoking caller-supplied stream methods. Mutable source rows alone are
    # intentionally consumed later, under a single forward cursor.
    if not isinstance(outputs, (list, tuple)) or len(outputs) != planned["volumeCount"] or any(type(p) is not VolumeStreams for p in outputs):
        raise AnalysisContractError("调用者临时输出流数量须与完整卷数一致")
    outputs = tuple(VolumeStreams(pair.xlsx, pair.html) for pair in outputs)
    frozen_tables = []
    for table in tables:
        columns = []
        for col in table.columns:
            if type(col) is not Column or col.ratio_of is not None and (type(col.ratio_of) not in (list, tuple) or len(col.ratio_of) != 2):
                raise AnalysisContractError("分卷列声明或比率引用无效")
            columns.append(Column(col.key, col.label, col.kind, col.total, tuple(col.ratio_of) if col.ratio_of is not None else None))
        frozen_tables.append(Table(table.key, table.title, table.note, tuple(columns), table.rows, table.row_count))
    tables = tuple(frozen_tables)
    _streams(outputs, planned["volumeCount"])
    # Detach the bounded descriptor/column containers; rows stay lazy and shared
    # only by consecutive fragments of their exact source table.
    sources = {t.key: _Source(t) for t in tables}
    volumes = []
    for volume, output in zip(planned["volumes"], outputs):
        index = volume["volumeIndex"]
        if checkpoint:
            checkpoint({"stage": "preparing_volume", "volumeIndex": index, "volumeCount": planned["volumeCount"]})
        fragments, expected_hashes = [], []
        for part in volume["tables"]:
            source = sources[part["key"]]
            rows, sha = source.fragment(part)
            note = source.table.note+f'\n完整多卷交付的表片段 {part["fragmentIndex"]}/{part["fragmentCount"]}；原表半开区间 [{part["rowOffset"]},{part["rowOffset"]+part["rowLimit"]})，共 {part["rowCount"]} 行。此片合计仅覆盖本片；须同时核验完整交付清单。'
            fragments.append(Table(part["fragmentKey"], source.table.title, note, source.table.columns, rows, part["rowLimit"]))
            expected_hashes.append(sha)
        binding = {"schemaVersion": "business-volume-part-v1", "reportId": report_id,
                   "evidenceDigest": evidence_digest, "rendererVersion": renderer_version,
                   "planDigest": planned["planDigest"], "volumeIndex": index, "volumeCount": planned["volumeCount"],
                   "publication": "requires_complete_multivolume_manifest", "fragments": volume["tables"]}
        if slim_html:
            binding["htmlPayloadVersion"] = 2
        def progress(value):
            if checkpoint:
                checkpoint({**value, "volumeIndex": index, "volumeCount": planned["volumeCount"]})
        proof = report_files.write_pair(_LimitedStream(output.xlsx, max_file_bytes), _LimitedStream(output.html, max_file_bytes),
            title=f"{title} · 第{index}/{planned['volumeCount']}卷", metadata={**metadata, "volumeDelivery": binding},
            tables=fragments, checkpoint=progress if checkpoint else None,
            offline_budget=offline_budget if index == 1 else None, excel_budget=excel_budget if index == 1 else None,
            html_layout_version=2 if renderer_version >= 4 else 1,
            xlsx_opc_version=2 if renderer_version in (6, 7, 9, 10, 11) else 1,
            html_payload_version=2 if slim_html else 1)
        if len(proof["tables"]) != len(volume["tables"]):
            raise AnalysisContractError("分片writer回执数量不一致")
        if renderer_version == 11 and type(proof.get("htmlPayload")) is not dict:
            raise AnalysisContractError("renderer 11 缺少压缩行原生证明")
        table_proofs = []
        for part, actual, sha in zip(volume["tables"], proof["tables"], expected_hashes):
            if (actual["key"], actual["rowCount"], actual["columnCount"], actual["rowDigest"]) != (part["fragmentKey"], part["rowLimit"], part["columnCount"], sha.hexdigest()):
                raise AnalysisContractError("分片同源行摘要或完整边界核对失败")
            table_proofs.append({**part, "sheet": actual["sheet"], "rowDigest": actual["rowDigest"], "precisionTextCells": actual["precisionTextCells"]})
        files = {}
        for format, stream in (("html", output.html), ("xlsx", output.xlsx)):
            files[format] = {"filename": f"{report_id}-volume-{index:03}-of-{planned['volumeCount']:03}.{format}",
                             **_file_proof(stream, max_file_bytes, progress if checkpoint else None)}
        volumes.append({"volumeIndex": index, "volumeCount": planned["volumeCount"], "kind": volume["kind"],
                        "nativeBudgetSheets": volume["nativeBudgetSheets"], "rowCount": sum(p["rowLimit"] for p in table_proofs),
                        "offlineBudgetEnabled": index == 1 and offline_budget is not None,
                        "tables": table_proofs, "files": files, **({"budgetCalculator": proof["budgetCalculator"]} if "budgetCalculator" in proof else {}),
                        **({"htmlPayload": proof["htmlPayload"]} if renderer_version == 11 else {})})
    source_proofs = []
    for descriptor in planned["tables"]:
        source = sources[descriptor["key"]]
        if not source.finished or source.position != descriptor["rowCount"]:
            raise AnalysisContractError("存在未结束或未覆盖的完整来源，禁止发布全部卷")
        source_proofs.append({**descriptor, "rowDigest": source.digest.hexdigest()})
    if sum(v["rowCount"] for v in volumes) != planned["totalRows"]:
        raise AnalysisContractError("多卷总行数与完整来源不一致")
    if checkpoint:
        checkpoint({"stage": "verifying_complete_delivery", "volumeCount": planned["volumeCount"]})
    # No further callbacks/source reads after this final pass: a later volume's
    # callback may have overwritten an earlier caller-owned stream. Do not bless
    # its new hash with an old row proof; fail the entire delivery instead.
    for volume, output in zip(volumes, outputs):
        for format, stream in (("html", output.html), ("xlsx", output.xlsx)):
            actual = _file_proof(stream, max_file_bytes, None)
            if any(actual[key] != volume["files"][format][key] for key in ("bytes", "sha256")):
                raise AnalysisContractError("先前卷文件在完整交付前变化，禁止发布全部卷")
    manifest = {"schemaVersion": "business-volume-files-v1", "status": "complete", "reportId": report_id,
                "evidenceDigest": evidence_digest, "rendererVersion": renderer_version,
                "planDigest": planned["planDigest"], "sourceDescriptorDigest": planned["sourceDescriptorDigest"],
                "volumeCount": planned["volumeCount"], "sourceTableCount": planned["sourceTableCount"],
                "fragmentCount": planned["fragmentCount"], "totalRows": planned["totalRows"],
                "byteCapacity": {"verified": True, "maxFileBytes": max_file_bytes, "dynamicByteSplitting": False},
                "tables": source_proofs, "volumes": volumes,
                **({"budgetPlanDigest": (offline_budget or excel_budget)["planDigest"]} if offline_budget is not None or excel_budget is not None else {})}
    mapping_keys = {"mappingPlanDigest", "mappingAlgorithmVersion", "mappedTableAlgorithmVersion"}
    if mapping_keys & metadata.keys():
        if not mapping_keys <= metadata.keys():
            raise AnalysisContractError("商品关联文件绑定字段不完整")
        manifest.update({key:metadata[key] for key in mapping_keys})
    from .volume_delivery import screening_fields
    manifest.update(screening_fields(metadata, report_id))
    if renderer_version in (7, 9):
        proof = metadata.get("promotionFileProof")
        if type(proof) is not dict:
            raise AnalysisContractError("推广渲染缺少固定词货文件证明")
        manifest["promotionFileProof"] = proof
    if renderer_version == 9:
        proof = metadata.get("promotionTrialProof")
        if type(proof) is not dict or offline_budget is not None or excel_budget is not None:
            raise AnalysisContractError("renderer 9 缺少试用证明或意外包含预算")
        manifest["promotionTrialProof"] = proof
        manifest["tableSchemaDigest"] = proof.get("tableSchemaDigest")
    if renderer_version in (10, 11):
        manifest["promotionFileProof"] = metadata["promotionFileProof"]
        manifest["promotionTrialProof"] = metadata["promotionTrialProof"]
        manifest["promotionBudgetProof"] = metadata["promotionBudgetProof"]
        manifest["tableSchemaDigest"] = metadata["tableSchemaDigest"]
    if renderer_version == 11:
        from .volume_delivery import slim_proof_v11
        manifest["promotionSlimProof"] = slim_proof_v11(manifest)
    return {**manifest, "manifestDigest": digest(manifest)}

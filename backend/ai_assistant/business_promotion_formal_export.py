"""Temporary approved HTML/XLSX pair for a promotion report; no delivery run.

The yielded paths live only inside this context. Success means both temporary
files and their renderer-7 proof were verified against live approved content;
it does not register a producer, authorize delivery, or permit DB ready.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from business_analysis import report_files
from business_analysis.contracts import AnalysisContractError
from business_analysis.results import VIEWS
from . import business_promotion_approved_content as approved_content
from . import business_promotion_content_contract as content_contract
from . import business_promotion_file_proof as file_proof
from . import business_promotion_file_tables as file_tables
from . import business_evidence, business_export, business_sealed_source_tables, models as m
from .business_sealed import Reader
from .policy import AiError, canonical, current_principal, digest


SCHEMA = "business-promotion-formal-pair-v1"
FRAGMENT_CHARS = 7000


def _conflict(message="词货正式临时文件与当前已批准报告不一致"):
    raise AiError(message, "conflict", 409)


def _fragments(value):
    raw = canonical(value)
    return [raw[offset:offset + FRAGMENT_CHARS]
        for offset in range(0, max(1, len(raw)), FRAGMENT_CHARS)]


def _tables(value, promotion):
    content = value["content"]
    text = (report_files.Column("item", "项目"), report_files.Column("content", "内容"))
    summary = [("报告ID", value["binding"]["reportId"]),
        ("总体判断", content["diagnosis"]["summary"]),
        ("人审状态", "已批准"),
        ("来源当前期", value["binding"]["promotionSelector"]["sourceKey"]),
        ("基期来源", value["binding"]["promotionSelector"].get("baselineKey") or "无"),
        ("词货费用", "两表为同一事实的不同分组，不可相加"),
        ("执行状态", "调整建议须人工执行和观察；本文件不触发广告修改")]
    sections = [[section["title"], section["body"]] for section in content["sections"]]
    finding_rows = []
    for index, finding in enumerate(content["diagnosis"]["findings"]):
        parts = _fragments(finding)
        for position, part in enumerate(parts, 1):
            finding_rows.append([index, finding.get("title") or finding.get("id") or "结论",
                finding.get("kind") or "未分类", position, len(parts), part])
    complete_rows = []
    complete_parts = _fragments(content)
    for position, part in enumerate(complete_parts, 1):
        complete_rows.append([position, len(complete_parts), part])
    coverage_rows = []
    for index, record in enumerate(content["screening"]["coverage"]):
        parts = _fragments(record)
        for position, part in enumerate(parts, 1):
            coverage_rows.append([index, position, len(parts), part])
    return (
        report_files.Table("business-summary", "经营摘要", "已批准五角色内容的可读摘要；词货双表费用不可相加。",
            text, summary, len(summary)),
        report_files.Table("diagnosis-sections", "深度诊断", "五个固定章节的完整正文。",
            (report_files.Column("section", "章节"), report_files.Column("body", "正文")),
            sections, len(sections)),
        report_files.Table("adjustment-findings", "调整规划与证据", "每条结论的完整规范JSON按片序拼接，保留动作前提与回退条件。",
            (report_files.Column("index", "结论位置", "integer"), report_files.Column("title", "标题"),
                report_files.Column("kind", "类型"), report_files.Column("fragment", "片号", "integer"),
                report_files.Column("fragments", "总片数", "integer"), report_files.Column("json", "规范JSON片段")),
            finding_rows, len(finding_rows)),
        report_files.Table("screening-coverage", "来源覆盖与缺口", "完整筛查覆盖逐记录保留；缺失不补零，候选金额不跨分区加总。",
            (report_files.Column("index", "记录位置", "integer"), report_files.Column("fragment", "片号", "integer"),
                report_files.Column("fragments", "总片数", "integer"), report_files.Column("json", "规范JSON片段")),
            coverage_rows, len(coverage_rows)),
        report_files.Table("complete-content", "完整已核验内容", "规范JSON按片号拼接，可核对五角色分析、独立复核、筛查与预算。",
            (report_files.Column("fragment", "片号", "integer"), report_files.Column("fragments", "总片数", "integer"),
                report_files.Column("json", "规范JSON片段")), complete_rows, len(complete_rows)),
        *promotion,
    )


@contextmanager
def _source_tables(report_id, principal, fixed, checkpoint, *, trial=False):
    """Reuse the v6 append order against this report's immutable sealed Reader."""
    report = m.AiReportRun.objects.select_related("workflow").get(pk=report_id)
    roots = fixed["rootBindings"]
    if (digest(report.snapshot_json) != fixed["snapshotDigest"]
            or digest(report.workflow.input_json) != fixed["workflowInputDigest"]):
        _conflict("完整来源表不属于当前报告版本")
    evidence = business_evidence.get_run(roots["evidenceRunId"], principal)
    if (evidence.status != "sealed" or evidence.version != roots["evidenceVersion"]
            or digest(evidence.plan_json) != json.loads(report.snapshot_json)["evidencePlanDigest"]):
        _conflict("完整来源表证据不是当前封存版本")
    reader = Reader(evidence, principal)
    sources = reader.sources
    if (len(sources) != roots["sourceCount"] or digest(sources) != roots["sourcesDigest"]):
        _conflict("完整来源目录与已批准内容不同")
    source_by_key = {source["key"]: source for source in sources}
    info = {source["key"]: reader.info(source["key"]) for source in sources}
    expected = {key: entry["expected"] for key, entry in info.items()}
    def pages(key):
        return reader.pages(key, checkpoint=checkpoint)
    with business_export.TableSpool() as spool:
        spool.add("sources", "来源与核对", "明细封存时的水位与逐页核对结果。",
            ({"sourceKey": source["key"], "来源": source["domain"],
                "查询范围": canonical(source["query"]),
                "核对": canonical(expected[source["key"]]),
                "覆盖与口径": canonical(info[source["key"]]["metadata"])}
                for source in sources))
        if trial:
            from business_analysis.promotion_trial_scope import tables as trial_scope_tables
            spool.tables.extend(trial_scope_tables(sources, info))
        business_sealed_source_tables.append(spool, sources, expected, pages,
            source_by_key, VIEWS, business_export.DIMENSION_NAMES)
        yield tuple(spool.tables), {"sourceCount": len(sources),
            "sourcesDigest": digest(sources), "sourceTableCount": len(spool.tables)}
    latest = Reader(business_evidence.get_run(roots["evidenceRunId"], principal), principal)
    if digest(latest.sources) != roots["sourcesDigest"]:
        _conflict("完整来源表生成期间封存目录发生变化")


def _file(path):
    sha = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while part := stream.read(512 * 1024):
            size += len(part)
            if size > report_files.MAX_FILE_BYTES:
                raise AiError("词货正式临时文件超过单文件容量", "payload_too_large", 413)
            sha.update(part)
    if size == 0:
        _conflict("词货正式临时文件为空")
    return {"bytes": size, "sha256": sha.hexdigest()}


@dataclass(frozen=True, slots=True)
class PreparedFormalFiles:
    _manifest: str
    _paths: dict
    _active: list

    @property
    def manifest(self):
        if not self._active[0]: _conflict("正式临时文件已离开生命周期")
        return json.loads(self._manifest)

    def path(self, kind):
        if not self._active[0] or kind not in ("html", "xlsx"):
            _conflict("正式临时文件不存在或已关闭")
        return self._paths[kind]


@contextmanager
def open_files(report_id, principal, *, checkpoint=None, limits=None):
    """Yield two complete temporary files; verify again after caller returns."""
    current_principal(principal, admin=True)
    with TemporaryDirectory(prefix="teruisi-promotion-formal-") as folder:
        paths = {kind: Path(folder) / ("report." + kind) for kind in ("html", "xlsx")}
        active = [True]
        try:
            with file_tables.open_tables(report_id, principal, draft=False,
                    checkpoint=checkpoint, limits=limits) as (metadata, promotion):
                fixed = metadata.value
                approved = metadata.approved_content
                if type(approved) is not content_contract.PreparedContent:
                    _conflict("正式文件缺少实际批准内容")
                value = approved.value
                material = fixed["sourceSummary"]["sourceMaterials"]
                fragment = file_proof.prepare(approved, material).value
                if (fragment["materialManifestDigest"] != fixed["materialManifestDigest"]
                        or fragment["contentDtoDigest"] != fixed["reportBinding"]["approvedContentDigest"]):
                    _conflict("词货文件证明与两张来源表不一致")
                file_metadata = {"schemaVersion": SCHEMA, "reportId": report_id,
                    "fileProof": fragment, "deliveryAuthorized": False,
                    "registeredRenderer": False, "limitations": list(content_contract.LIMITATIONS)}
                file_metadata["metadataDigest"] = digest(file_metadata)
                with _source_tables(report_id, principal, fixed["reportBinding"], checkpoint) as (sealed, source_proof):
                    base = _tables(value, promotion)
                    tables = (*base[:-2], *sealed, *base[-2:])
                    if len(tables) > report_files.MAX_TABLES:
                        raise AiError("完整封存来源表超过单卷120表容量，须使用后续多卷交付", "payload_too_large", 413)
                    file_metadata.update(sealedSourceCount=source_proof["sourceCount"],
                        sealedSourcesDigest=source_proof["sourcesDigest"],
                        sealedSourceTableCount=source_proof["sourceTableCount"])
                    file_metadata["metadataDigest"] = digest({key: item for key, item in file_metadata.items()
                        if key != "metadataDigest"})
                    with paths["xlsx"].open("w+b") as xlsx, paths["html"].open("w+b") as html:
                        rendered = report_files.write_pair(xlsx, html,
                            title="词货深度经营分析 · " + report_id,
                            metadata=file_metadata, tables=tables, checkpoint=checkpoint,
                            html_layout_version=2, xlsx_opc_version=2)
                if ([part["rowCount"] for part in rendered["tables"][-2:]] !=
                        [part["rowCount"] for part in fragment["tables"]]):
                    _conflict("词货两表渲染行数与完整材料不同")
            # file_tables has closed both owning contexts and checked current
            # roots. Rebuild actual approval once more before exposing paths.
            latest = approved_content.build(report_id, principal)
            if latest["dtoDigest"] != value["dtoDigest"]:
                _conflict("文件生成后五角色内容或人审发生变化")
            files = {kind: _file(path) for kind, path in paths.items()}
            receipt = {"schemaVersion": SCHEMA, "reportId": report_id,
                "rendererVersion": 7, "fileProof": fragment,
                "renderedTables": rendered["tables"], "files": files,
                "sealedSourceCount": source_proof["sourceCount"],
                "sealedSourcesDigest": source_proof["sourcesDigest"],
                "sealedSourceTableCount": source_proof["sourceTableCount"],
                "deliveryAuthorized": False, "registeredRenderer": False,
                "authorityVerified": False}
            receipt["receiptDigest"] = digest(receipt)
            prepared = PreparedFormalFiles(canonical(receipt), paths, active)
            yield prepared
            if {kind: _file(path) for kind, path in paths.items()} != files:
                _conflict("生成后的临时文件在交接前发生变化")
            if approved_content.build(report_id, principal)["dtoDigest"] != value["dtoDigest"]:
                _conflict("交接期间五角色内容或人审发生变化")
            current_principal(principal, admin=True)
        except (AnalysisContractError, file_proof.FileProofError) as error:
            raise AiError("词货正式双文件或证明校验失败", "conflict", 409) from error
        finally:
            active[0] = False

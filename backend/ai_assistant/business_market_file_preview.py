"""Unregistered data-only HTML/XLSX pair from complete market sample tables.

The three files' tables are source-bound evidence views, not a deep diagnosis.
Temporary paths live only inside the context. No persistent file run, model
call, delivery authorization or business mutation occurs here.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from business_analysis import market_report_tables, report_files
from business_analysis.contracts import AnalysisContractError
from . import business_market_export as export
from .policy import AiError, canonical, current_principal, digest


SCHEMA = "business-market-file-preview-v1"
NOTES = (
    "京东逐日TOP只是榜单样本，不代表全行业、店铺销售或B端销售。",
    "某日缺席TOP样本不等于销量为零，也不证明商品退出经营。",
    "价格带保留区间上下界和缺失；价格带汇总与成员明细来自同一事实，不可相加。",
    "进出榜只是固定两日的样本观察，不证明利润、商品归属或市场因果变化。",
)


def _conflict(message="市场临时文件与当前封存报告不一致"):
    raise AiError(message, "conflict", 409)


def _file(path):
    sha, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while part := stream.read(512 * 1024):
            size += len(part)
            if size > report_files.MAX_FILE_BYTES:
                raise AiError("市场临时文件超过单文件容量", "payload_too_large", 413)
            sha.update(part)
    if size == 0: _conflict("市场临时文件为空")
    return {"bytes": size, "sha256": sha.hexdigest()}


@dataclass(frozen=True, slots=True)
class PreparedMarketPreview:
    _manifest: str
    _paths: dict
    _active: list

    @property
    def manifest(self):
        if not self._active[0]: _conflict("市场预览临时文件已关闭")
        return json.loads(self._manifest)

    def path(self, kind):
        if not self._active[0] or kind not in ("html", "xlsx"):
            _conflict("市场预览文件不存在或已关闭")
        return self._paths[kind]


@contextmanager
def open_files(report_id, source_key, baseline_key, principal, *, bands,
               checkpoint=None, limits=None):
    """Prepare exact three-table pair and recheck roots at the final exit."""
    current_principal(principal, admin=True)
    prepared = export.prepare(report_id, source_key, baseline_key, principal,
        bands=bands, checkpoint=checkpoint, limits=limits)
    if type(prepared) is not export.PreparedMarketExport:
        _conflict("市场文件不能接受公开DTO作为来源")
    manifest = prepared.manifest
    if (manifest.get("registeredRenderer") is not False
            or manifest.get("manifestDigest") != digest({key: value for key, value in manifest.items()
                if key != "manifestDigest"})
            or manifest.get("sourceKey") != source_key or manifest.get("baselineKey") != baseline_key
            or manifest.get("bands") != bands):
        _conflict("市场完整材料不是当前显式范围")
    fixed = manifest["reportBinding"]
    if fixed.get("reportId") != report_id:
        _conflict("市场材料属于其他报告")
    with TemporaryDirectory(prefix="teruisi-market-preview-") as folder:
        paths = {kind: Path(folder) / ("report." + kind) for kind in ("html", "xlsx")}
        active = [True]
        try:
            with market_report_tables.tables(manifest, {view: prepared.ndjson_pages(view)
                    for view in export.VIEWS}) as (summary, original_tables):
                if len(original_tables) != 3 or summary["authorityVerified"] is not False:
                    _conflict("市场样本三表未完整核验")
                metadata = {"schemaVersion": SCHEMA, "reportId": report_id,
                    "sourceKey": source_key, "baselineKey": baseline_key,
                    "bands": manifest["bands"], "materialManifestDigest": manifest["manifestDigest"],
                    "reportBindingDigest": digest(fixed), "notes": list(NOTES),
                    "authority": manifest["authority"], "dataOnly": True,
                    "diagnosisIncluded": False, "deliveryAuthorized": False,
                    "registeredRenderer": False}
                metadata["metadataDigest"] = digest(metadata)
                notes = "\n".join(NOTES)
                tables = tuple(report_files.Table(table.key, table.title,
                    table.note + "\n" + notes, table.columns, table.rows, table.row_count)
                    for table in original_tables)
                with paths["xlsx"].open("w+b") as xlsx, paths["html"].open("w+b") as html:
                    rendered = report_files.write_pair(xlsx, html,
                        title="市场TOP样本三表 · " + report_id,
                        metadata=metadata, tables=tables, checkpoint=checkpoint,
                        html_layout_version=2, xlsx_opc_version=2)
                if ([item["key"] for item in rendered["tables"]] !=
                        [table.key for table in original_tables]
                        or [item["rowCount"] for item in rendered["tables"]] !=
                        [item["rowCount"] for item in manifest["tables"]]):
                    _conflict("市场三表行数或顺序与完整材料不同")
            export.owning.report_binding._revalidate(fixed, principal)
            if checkpoint: checkpoint({"stage": "market_file_preview", "phase": "verified"})
            files = {kind: _file(path) for kind, path in paths.items()}
            receipt = {"schemaVersion": SCHEMA, "reportId": report_id,
                "materialManifestDigest": manifest["manifestDigest"],
                "reportBindingDigest": digest(fixed), "renderedTables": rendered["tables"],
                "files": files, "notes": list(NOTES), "dataOnly": True,
                "diagnosisIncluded": False, "deliveryAuthorized": False,
                "registeredRenderer": False, "authorityVerified": False}
            receipt["receiptDigest"] = digest(receipt)
            result = PreparedMarketPreview(canonical(receipt), paths, active)
            yield result
            if {kind: _file(path) for kind, path in paths.items()} != files:
                _conflict("交接期间市场临时文件发生变化")
            export.owning.report_binding._revalidate(fixed, principal)
            current_principal(principal, admin=True)
        except AnalysisContractError as error:
            raise AiError("市场三表或文件完整性校验失败", "conflict", 409) from error
        finally:
            active[0] = False

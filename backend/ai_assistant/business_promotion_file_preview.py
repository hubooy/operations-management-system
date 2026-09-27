"""Unregistered data-only paired preview; not diagnosis or file authorization.

Caller streams must be distinct, empty, seekable binary streams. Neither output
may be published until build returns normally. On failure both are rolled back.
"""
from dataclasses import replace
import hashlib
from tempfile import TemporaryFile

from business_analysis import report_files
from . import business_promotion_file_tables as owning
from .policy import AiError, digest

TITLE = "词货数据草稿（未形成诊断报告）"


def _empty(stream):
    try:
        if not stream.seekable() or not stream.writable(): raise ValueError("stream capabilities")
        position = stream.tell(); stream.seek(0, 2); size = stream.tell(); stream.seek(position)
        if position != 0 or size != 0: raise ValueError("nonempty stream")
    except (AttributeError, OSError, ValueError) as error:
        raise AiError("预览输出须使用独立空白可定位二进制临时流") from error


def _copy_verified(source, target):
    source.seek(0)
    count, sha = 0, hashlib.sha256()
    while raw := source.read(64 * 1024):
        count += len(raw)
        if count > report_files.MAX_FILE_BYTES: raise AiError("完整预览超过文件容量", "payload_too_large", 413)
        if target.write(raw) != len(raw): raise OSError("preview output short write")
        sha.update(raw)
    return {"bytes": count, "sha256": sha.hexdigest()}


def build(report_id, principal, xlsx_stream, html_stream, *, checkpoint=None, limits=None):
    if xlsx_stream is html_stream: raise AiError("HTML和XLSX必须使用不同临时流")
    _empty(xlsx_stream); _empty(html_stream)
    try:
        with TemporaryFile("w+b") as xlsx, TemporaryFile("w+b") as html:
            with owning.open_tables(report_id, principal, draft=True, checkpoint=checkpoint, limits=limits) as (fixed, tables):
                metadata = fixed.value
                manifest = metadata["sourceSummary"]["sourceMaterials"]
                if metadata["materialManifestDigest"] != manifest["manifestDigest"]:
                    raise AiError("预览材料清单绑定不一致", "conflict", 409)
                descriptions = []
                for table, item in zip(tables, manifest["tables"], strict=True):
                    header = item["header"]
                    source = header["source"]
                    before = header["baselineSource"]
                    source_text = f"来源：{source['key']}，{source['query']['platform']} / {source['query']['shop']}。"
                    periods = header["periods"]
                    current = periods[header["sourceWindow"]]
                    dates = f"本期：{current['startDate']} 至 {current['endDate']}。"
                    if before is not None:
                        period = periods[header["comparisonWindow"]]
                        baseline = f"基期：{before['key']}，{header['comparisonWindow']}，{period['startDate']} 至 {period['endDate']}。"
                    else: baseline = "基期：未选择，不提供跨期结论。"
                    note = "仅为数据草稿，未形成诊断结论或调整建议。" + source_text + dates + baseline + table.note
                    descriptions.append(replace(table, note=note))
                preview_metadata = {"schemaVersion": "business-promotion-data-preview-v1", "dataOnlyDraft": True,
                    "diagnosisIncluded": False, "deliveryAuthorized": False, "registeredRenderer": False,
                    "materialManifestDigest": manifest["manifestDigest"], "owningMetadata": metadata}
                proof = report_files.write_pair(xlsx, html, title=TITLE, metadata=preview_metadata,
                    tables=tuple(descriptions), checkpoint=checkpoint, html_layout_version=2, xlsx_opc_version=2)
                files = {"xlsx": _copy_verified(xlsx, xlsx_stream), "html": _copy_verified(html, html_stream)}
            # open_tables final live report/actor check has now succeeded.
            result = {**preview_metadata, "tableProof": proof, "files": files}
            result["previewDigest"] = digest(result)
            return result
    except BaseException:
        # Keep existing write errors intact; rollback is best effort if a caller
        # supplied a broken device. Such streams must never be published.
        for stream in (xlsx_stream, html_stream):
            try: stream.seek(0); stream.truncate(0)
            except (OSError, ValueError): pass
        raise

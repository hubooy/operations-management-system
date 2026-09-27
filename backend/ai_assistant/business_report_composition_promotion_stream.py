"""Closed owning bridge: one sealed-v2 promotion source to private volumes.

This proves that the volume writer accepts an actual same-report Reader.pages
iterator. It deliberately retains v2's 2,000-page/64 MiB ceiling. A future
v4 report must first gain a versioned same-report source bridge; collecting or
sealed v4 runs are not interchangeable with an AiReportRun's v2 evidence.
"""
from __future__ import annotations

import hashlib
import time

from business_analysis import (cross_source_kpi_plan as planning,
    report_composition_volume_stream_v1 as volume)
from business_analysis.contracts import AnalysisContractError
from business_analysis.report_files import Column

from . import business_cross_source_daily_materials as daily_owner
from . import business_report_composition_owning as report_owner
from .business_sealed import Reader
from .policy import AiError, canonical, digest


SCHEMA = "business-report-composition-promotion-stream-owning-v1"
MAX_SECONDS = 1200
COLUMNS = (Column("sourceKey", "封存推广来源键"),
    Column("rowIndex", "来源连续行号", "integer"),
    Column("sourceRowJson", "完整推广规范来源行"))


def _need(ok, message="推广明细流不属于当前同一封存报告"):
    if not ok:
        raise AiError(message, "conflict", 409)


def _binding(report_id, principal, base, selected):
    snapshot, evidence, sources, fixed, infos = report_owner._snapshot(
        report_id, principal, selected)
    _need(base["reportBinding"] == fixed
        and base["sourceEvidenceDigests"] == {key:
            infos[key]["expected"]["evidenceDigest"] for key in selected}
        and base["sourceRevisions"] == {key:
            infos[key]["metadata"]["sourceRevision"] for key in selected}
        and base["sourceKeys"] == daily_owner._selection(base["sourceKeys"]),
        "报告、封存或来源修订已变化")
    context = {"reportId": fixed["reportId"],
        "evidenceRunId": evidence.id, "evidenceVersion": evidence.version,
        "sealedDigest": snapshot["sealedDigest"],
        "ownerEmail": principal.email.lower(), "scope": principal.scope}
    plan = planning.prepare_candidate(sources, infos, context,
        base["sourceKeys"])
    _need(plan["planDigest"] == base["planDigest"]
        and plan["mappingPlan"] == snapshot["mappingPlan"]
        and plan["mappingPlanDigest"] == snapshot["mappingPlanDigest"])
    return evidence, sources, infos, fixed


def _rows(reader, source_key, expected_count, *, deadline, checkpoint):
    count = 0
    for sequence, page in enumerate(reader.pages(source_key,
            checkpoint=checkpoint), 1):
        _need(time.monotonic() < deadline,
            "推广来源双遍读取超过固定时间边界")
        for item in page["items"]:
            _need(type(item) is dict)
            raw = canonical(item)
            _need(len(raw.encode("utf-16-le")) // 2 <= 32767,
                "完整推广行超过Excel单元格容量，须版本化分列")
            yield (source_key, count, raw)
            count += 1
        if checkpoint:
            checkpoint({"stage": "composition_promotion_source",
                "phase": "page_complete", "sourceKey": source_key,
                "pageSequence": sequence, "rows": count})
    _need(count == expected_count,
        "完整推广来源行数与封存控制汇总不一致")


def prepare(report_id, base_manifest, source_key, principal, stage_sink,
            *, enabled=False, max_volume_rows=volume.MAX_VOLUME_ROWS,
            checkpoint=None):
    """Replay one v2 source twice, stage paired volumes, recheck authority.

    Full source row JSON is retained as a text column without changing its
    numerical meaning. No partial page or unfinished iterator can publish.
    """
    _need(enabled is True, "同报告推广来源流默认关闭")
    volume._base(base_manifest)
    _need(type(source_key) is str
        and type(base_manifest.get("sourceKeys")) is dict
        and source_key in base_manifest["promotionSourceKeys"])
    keys = daily_owner._selection(base_manifest["sourceKeys"])
    selected = report_owner.sku_owner._selected(keys)
    deadline = time.monotonic() + MAX_SECONDS
    try:
        evidence, sources, infos, fixed = _binding(report_id, principal,
            base_manifest, selected)
        source = next(item for item in sources if item["key"] == source_key)
        _need(source["domain"] == "netshop"
            and source["query"].get("dataset") == "promotion"
            and source["query"].get("platform") == "京东")
        expected_count = infos[source_key]["expected"]["rowCount"]
        _need(type(expected_count) is int and
            0 <= expected_count <= planning.MAX_V2_SOURCE_PAGES *
                planning.MAX_V2_PAGE_ROWS)
        def read():
            return _rows(Reader(evidence, principal), source_key,
                expected_count, deadline=deadline, checkpoint=checkpoint)
        root = hashlib.sha256()
        count = 0
        for row in read():
            root.update((canonical(list(row)) + "\n").encode("utf-8"))
            count += 1
        _need(count == expected_count and time.monotonic() < deadline)
        _binding(report_id, principal, base_manifest, selected)
        source_body = {"schemaVersion": volume.SCHEMA,
            "reportBindingDigest": digest(fixed),
            "planDigest": base_manifest["planDigest"],
            "sourceKey": source_key,
            "sourceEvidenceDigest": infos[source_key]["expected"][
                "evidenceDigest"],
            "sourceRevision": infos[source_key]["metadata"][
                "sourceRevision"],
            "rowCount": count, "rowDigest": root.hexdigest()}
        source_manifest = {**source_body,
            "manifestDigest": digest(source_body)}
        def current(_base, _source):
            _need(time.monotonic() < deadline)
            _binding(report_id, principal, base_manifest, selected)
            return True
        candidate = volume.build_candidate(base_manifest, source_manifest,
            COLUMNS, read(), stage_sink, current, enabled=True,
            max_volume_rows=max_volume_rows, checkpoint=checkpoint)
        body = {"schemaVersion": SCHEMA,
            "reportBindingDigest": digest(fixed),
            "sourceManifestDigest": source_manifest["manifestDigest"],
            "volumeResultDigest": candidate["resultDigest"],
            "sourceRowCount": count,
            "completeSealedV2PagesReplayedTwice": True,
            "sourceCapacityExtendedBeyondV2": False,
            "stagedUnpublished": True,
            "agentReadPersisted": False, "registeredRenderer": False}
        return {"sourceManifest": source_manifest,
            "volumeManifest": candidate,
            "owningReceipt": {**body, "receiptDigest": digest(body)}}
    except (AnalysisContractError, KeyError, TypeError, ValueError,
            UnicodeError, OverflowError, StopIteration) as error:
        raise AiError("同报告推广明细未通过封存双遍页链与分卷核验",
            "conflict", 409) from error

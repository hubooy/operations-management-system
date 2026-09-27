"""Versioned evidence storage. V2 directories never expand the fact quota.

Callers authorize the parent before using these internal helpers. Source facts
remain in the existing immutable ledger; only bounded checkpoints move here.
"""
import json
from django.db.models import Sum, Count, Q, F, Func, BigIntegerField
from business_analysis import evidence_v2
from business_analysis.contracts import AnalysisContractError, PageReconciler
from . import models as m
from .policy import AiError, canonical, digest


def is_v2(row):
    try:
        plan = json.loads(row.plan_json)
    except (ValueError, TypeError) as error:
        raise AiError("证据计划无效", "conflict", 409) from error
    schema = plan.get("schemaVersion") if isinstance(plan, dict) else None
    if schema not in ("business-evidence-v1", evidence_v2.HEADER_SCHEMA):
        raise AiError("证据计划版本不支持", "conflict", 409)
    return schema == evidence_v2.HEADER_SCHEMA


def catalog(row):
    """Rebuild from at most 48 persisted identities, never a supplied digest."""
    if not is_v2(row):
        return json.loads(row.plan_json)["sources"]
    try:
        header = json.loads(row.plan_json)
        records = list(m.AiBusinessEvidenceSource.objects.filter(run_id=row.id).order_by("ordinal").values(
            "source_key", "ordinal", "domain", "query_json", "query_digest")[:49])
        sources = [{"key": r["source_key"], "domain": r["domain"], "query": json.loads(r["query_json"])} for r in records]
        rebuilt = evidence_v2.build_catalog(sources, analysis_request=header.get("analysisRequest"))
        evidence_v2.validate_header(header, sources, analysis_request=header.get("analysisRequest"))
        actual = [{"key": r["source_key"], "ordinal": r["ordinal"], "domain": r["domain"],
                   "query": json.loads(r["query_json"]), "queryDigest": r["query_digest"]} for r in records]
        if canonical(actual) != canonical(rebuilt["entries"]):
            raise AnalysisContractError("持久来源身份、顺序或摘要不一致")
    except (AnalysisContractError, ValueError, TypeError, KeyError) as error:
        raise AiError("证据来源目录未通过核验", "conflict", 409) from error
    return [{k: e[k] for k in ("key", "domain", "query")} for e in rebuilt["entries"]]


def source_record(row, key, *, lock=False):
    records = m.AiBusinessEvidenceSource.objects.filter(run_id=row.id, source_key=key)
    record = (records.select_for_update() if lock else records).first()
    if record is None:
        raise AiError("来源不存在", "not_found", 404)
    return record


def checkpoint(record):
    try:
        value = json.loads(record.checkpoint_json)
        if record.page_count == 0:
            if value != {} or record.row_count or record.stored_bytes or record.finished:
                raise ValueError("invalid empty checkpoint")
            return None
        if type(value) is not dict or set(value) != {"pageCount", "verifier", "metadata"}:
            raise ValueError("checkpoint fields")
        verifier = PageReconciler()
        if type(value["verifier"]) is not dict or set(value["verifier"]) != set(verifier.__dict__) or type(value["metadata"]) is not dict:
            raise ValueError("checkpoint shape")
        verifier.__dict__.update(value["verifier"])
        if (type(value["pageCount"]) is not int or value["pageCount"] != record.page_count
                or type(verifier.rows) is not int or verifier.rows != record.row_count
                or type(verifier.finished) is not bool or verifier.finished != record.finished):
            raise ValueError("checkpoint counters")
        if record.finished:
            verifier.result()
        return value
    except (AnalysisContractError, ValueError, TypeError, KeyError) as error:
        raise AiError("来源检查点未通过核验", "conflict", 409) from error


def source_state(row, key):
    if not is_v2(row):
        return json.loads(row.state_json).get(key)
    record = source_record(row, key)
    if record.checkpoint_run_version > row.version:
        raise AiError("证据读取期间版本已变化，请刷新", "version_conflict", 409)
    return checkpoint(record)


def assert_current(row):
    """Optimistic read fence: no old parent version with newer source counters."""
    actual = m.AiBusinessEvidenceRun.objects.filter(pk=row.id).values("version", "status").first()
    if actual != {"version": row.version, "status": row.status}:
        raise AiError("证据读取期间版本已变化，请刷新", "version_conflict", 409)


def progress(row):
    """Only fixed-size counter columns, not checkpoint JSON or fact payloads."""
    result = m.AiBusinessEvidenceSource.objects.filter(run_id=row.id).aggregate(
        sourceCount=Count("id"), completedSources=Count("id", filter=Q(finished=True)),
        rowCount=Sum("row_count"), pageCount=Sum("page_count"), storedBytes=Sum("stored_bytes"))
    return {k: v or 0 for k, v in result.items()}


def check_quota(principal, delta_bytes, max_fact_bytes):
    """SQL sums include all existing facts plus parent/directory JSON overhead.

    Called under the shared AI mutation lock. All facts retain the old accounting;
    only v2 adds its parent/header and source metadata to that same shared quota.
    """
    def octets(field):
        return Func(F(field), function="OCTET_LENGTH", output_field=BigIntegerField())
    def usage(owner=None):
        runs = m.AiBusinessEvidenceRun.objects.all()
        sources = m.AiBusinessEvidenceSource.objects.all()
        if owner is not None:
            runs = runs.filter(owner_email=owner)
            sources = sources.filter(run__owner_email=owner)
        facts = runs.aggregate(n=Sum("stored_bytes"))["n"] or 0
        v2_runs = runs.filter(id__in=sources.values("run_id"))
        parent = v2_runs.aggregate(n=Sum(octets("plan_json")+octets("state_json")))["n"] or 0
        directory = sources.aggregate(n=Sum(octets("query_json")+octets("checkpoint_json")))["n"] or 0
        return facts+parent+directory
    if usage(principal.email.lower())+delta_bytes > max_fact_bytes*4 or usage()+delta_bytes > max_fact_bytes*32:
        raise AiError("共享证据及目录元数据存储额度已满", "payload_too_large", 413)


def compact_sources(row):
    return {s["source_key"]: {"pageCount": s["page_count"], "rowCount": s["row_count"],
             "complete": s["finished"], "version": s["version"], "storedBytes": s["stored_bytes"]}
            for s in m.AiBusinessEvidenceSource.objects.filter(run_id=row.id).order_by("ordinal").values(
                "source_key", "page_count", "row_count", "finished", "version", "stored_bytes")[:49]}


def seal_value(row, *, target_version=None):
    """Bind all final checkpoints and immutable identities without copying them."""
    sources = catalog(row)
    proofs = []
    for source in sources:
        record = source_record(row, source["key"])
        value = checkpoint(record)
        if value is None or not record.finished:
            raise AiError("来源尚未全部核对完成", "conflict", 409)
        proofs.append({"sourceKey": record.source_key, "sourceVersion": record.version,
            "queryDigest": record.query_digest, "pageCount": record.page_count,
            "storedBytes": record.stored_bytes, "rowCount": record.row_count,
            "checkpointDigest": digest(record.checkpoint_json)})
    totals = progress(row)
    if totals["storedBytes"] != row.stored_bytes:
        raise AiError("来源事实字节与任务不一致", "conflict", 409)
    base = {"schemaVersion": "business-evidence-seal-v2", "runId": row.id,
        "evidenceVersion": target_version if target_version is not None else row.version,
        "planDigest": digest(row.plan_json), "catalogDigest": json.loads(row.plan_json)["catalogDigest"],
        "sourcesDigest": digest(proofs), **totals}
    return {**base, "sealedDigest": digest(base)}


def verify_seal(row):
    if row.status == "sealed" and is_v2(row):
        if row.state_json != canonical(seal_value(row)):
            raise AiError("封存证据摘要不一致", "conflict", 409)

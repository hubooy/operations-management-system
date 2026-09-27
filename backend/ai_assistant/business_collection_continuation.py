"""Bind owning-source continuation to AI ledger bytes; no business-domain ORM."""
from dataclasses import dataclass
import json
import re

from django.db.models import BigIntegerField, F, Func
from access_control.models import AppUser
from business_analysis.contracts import comparison_periods, digest as page_digest, SCHEMA_VERSION
from . import models as m, business_evidence_store as store
from .policy import AiError, canonical, digest

TOOL = "get_business_netshop_continuation_page"
TOOLS = {"netshop": TOOL, "sales": "get_business_sales_continuation_page",
         "market": "get_business_market_continuation_page"}
MAX_SAFE = 9007199254740991


def reject(message="来源续读检查点未通过核验"):
    raise AiError(message, "conflict", 409)


def actor_snapshot(principal):
    if principal.role != "admin" or principal.scope is not None:
        raise AiError("来源续读仅允许当前无范围管理员", "access_denied", 403)
    actor = AppUser.objects.filter(email=principal.email.lower()).values("email", "role", "scope", "status", "version").first()
    if (not actor or actor["role"] != "admin" or actor["scope"] is not None or actor["status"] != "active"
            or type(actor["version"]) is not int or actor["version"] < 1):
        raise AiError("实际账号或权限已变化", "access_denied", 403)
    return canonical(actor)


@dataclass(frozen=True)
class Prepared:
    run_id: str
    run_version: int
    source_id: str
    source_version: int
    checkpoint_json: str
    page_count: int
    actor: str
    arguments_json: str
    tool: str

    def arguments(self):
        return json.loads(self.arguments_json)


def check_actor(prepared, principal):
    if actor_snapshot(principal) != prepared.actor:
        raise AiError("续读期间账号或权限版本已变化", "access_denied", 403)


def check(prepared, principal):
    check_actor(prepared, principal)
    run = m.AiBusinessEvidenceRun.objects.filter(pk=prepared.run_id).values("version", "status").first()
    record = m.AiBusinessEvidenceSource.objects.filter(pk=prepared.source_id, run_id=prepared.run_id).values(
        "version", "checkpoint_json", "page_count", "finished").first()
    if (run != {"version": prepared.run_version, "status": "collecting"}
            or record != {"version": prepared.source_version, "checkpoint_json": prepared.checkpoint_json,
                          "page_count": prepared.page_count, "finished": False}):
        raise AiError("续读期间父任务或来源检查点已变化", "version_conflict", 409)


def prepare(run, source, record, principal):
    actor = actor_snapshot(principal)
    if (not store.is_v2(run) or source["domain"] not in TOOLS or record.domain != source["domain"]
            or record.run_id != run.id or record.source_key != source["key"] or run.status != "collecting"
            or not 1 <= record.page_count < 2000 or record.finished
            or not 1 <= record.checkpoint_run_version <= run.version
            or len(record.checkpoint_json.encode()) > 32768
            or canonical(source["query"]) != record.query_json or digest(record.query_json) != record.query_digest):
        reject()
    entry = store.checkpoint(record)
    if not entry:
        reject()
    if run.stored_bytes >= 64 * 1024 * 1024 or store.progress(run)["pageCount"] >= 2000:
        raise AiError("证据容量已满；保留原检查点", "payload_too_large", 413)
    state, metadata = entry["verifier"], entry["metadata"]
    if (state["finished"] is not False or type(state["last_id"]) is not int or not 1 <= state["last_id"] <= MAX_SAFE
            or type(state["source_ref"]) is not str or not re.fullmatch(r"[a-f0-9]{64}", state["source_ref"])
            or type(state["expected_cursor"]) is not str or not 1 <= len(state["expected_cursor"]) <= 1600
            or type(metadata.get("sourceRevision")) is not str or not 1 <= len(metadata["sourceRevision"]) <= 128):
        reject()
    chunks = m.AiBusinessEvidenceChunk.objects.filter(run_id=run.id, source_key=record.source_key)
    last = list(chunks.order_by("-sequence").annotate(payload_bytes=Func(F("payload_json"),
        function="OCTET_LENGTH", output_field=BigIntegerField())).values("id", "sequence", "payload_digest", "payload_bytes")[:1])
    if not last or last[0]["sequence"] != record.page_count or not 1 <= last[0]["payload_bytes"] <= 131072:
        reject()
    raw = chunks.filter(pk=last[0]["id"]).values_list("payload_json", flat=True).first()
    if type(raw) is not str or len(raw.encode()) != last[0]["payload_bytes"] or digest(raw) != last[0]["payload_digest"]:
        reject()
    try:
        page = json.loads(raw)
        pagination, items = page["pagination"], page["items"]
        expected = {k: v for k, v in source["query"].items() if k not in {"startDate", "endDate"}}
        if (page["schemaVersion"] != SCHEMA_VERSION or page["sourceRef"] != state["source_ref"]
                or page["sourceRevision"] != metadata["sourceRevision"]
                or type(pagination["limit"]) is not int or pagination["limit"] != 100
                or pagination["hasMore"] is not True or pagination["nextCursor"] != state["expected_cursor"]
                or type(items) is not list or not 1 <= len(items) <= 100
                or type(page["pageEvidence"]["rowCount"]) is not int or page["pageEvidence"]["rowCount"] != len(items)
                or page_digest(items) != page["pageEvidence"]["sha256"]
                or any(page["filters"].get(k) != v for k, v in expected.items())
                or page["filters"].get("periods") != comparison_periods(source["query"]["startDate"], source["query"]["endDate"])):
            reject()
        previous = 0
        for item in items:
            row_id = item["rowId"]
            if type(row_id) is str and re.fullmatch(r"[1-9][0-9]{0,15}", row_id):
                row_id = int(row_id)
            if type(row_id) is not int or not previous < row_id <= MAX_SAFE:
                reject()
            previous = row_id
        if previous != state["last_id"] or record.row_count < len(items):
            reject()
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise AiError("来源续读末块未通过核验", "conflict", 409) from error
    arguments = {**source["query"], "limit": 100, "cursor": state["expected_cursor"],
        "expectedSourceRef": state["source_ref"], "expectedRevision": metadata["sourceRevision"], "expectedLastId": state["last_id"]}
    prepared = Prepared(run.id, run.version, record.id, record.version, record.checkpoint_json,
        record.page_count, actor, canonical(arguments), TOOLS[source["domain"]])
    check(prepared, principal)
    return prepared

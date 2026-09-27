"""Bind a v3 daily continuation to the actual last immutable chunk.

Owning reader tools distinguish a current signed cursor from a genuinely
expired one. This module does not decode, renew, or accept a client cursor.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from business_analysis.contracts import MAX_SAFE_INTEGER, SCHEMA_VERSION, digest as row_digest
from . import business_evidence_v3 as plan, models as m
from .policy import AiError, canonical, digest

TOOLS = {"netshop": "get_business_netshop_continuation_page",
         "sales": "get_business_sales_continuation_page",
         "market": "get_business_market_continuation_page"}


def _reject(message="v3日来源续读末块或检查点无效"):
    raise AiError(message, "conflict", 409)


@dataclass(frozen=True, slots=True)
class Prepared:
    run_id: str
    run_version: int
    source_id: str
    source_version: int
    checkpoint_json: str
    actor_json: str
    tool: str
    arguments_json: str

    def arguments(self):
        return json.loads(self.arguments_json)


def check(prepared, principal):
    if canonical(plan._actor(principal)) != prepared.actor_json:
        raise AiError("v3续读期间账号权限变化", "access_denied", 403)
    parent = m.AiBusinessEvidenceRun.objects.filter(pk=prepared.run_id).values("version", "status", "collection_status").first()
    source = m.AiBusinessEvidenceSource.objects.filter(pk=prepared.source_id, run_id=prepared.run_id).values(
        "version", "checkpoint_json", "finished").first()
    if (parent != {"version": prepared.run_version, "status": "collecting", "collection_status": "manual"}
            or source != {"version": prepared.source_version, "checkpoint_json": prepared.checkpoint_json,
                          "finished": False}):
        raise AiError("v3续读期间父任务或来源CAS已变化", "version_conflict", 409)


def prepare(row, source, query, snapshot, principal):
    actor = canonical(plan._actor(principal))
    if (source.domain not in TOOLS or source.run_id != row.id or row.status != "collecting"
            or row.collection_status != "manual" or source.source_key != snapshot["sourceKey"]
            or source.id != snapshot["sourceId"] or source.version != snapshot["sourceVersion"]
            or row.version != snapshot["runVersion"] or source.finished
            or not 1 <= source.page_count < 1999 or source.checkpoint_json == "{}"
            or source.query_json != canonical(query)):
        _reject()
    try:
        checkpoint = json.loads(source.checkpoint_json)
        state, metadata = checkpoint["verifier"], checkpoint["metadata"]
        cursor, source_ref, revision, last_id = (state["expected_cursor"], state["source_ref"],
                                                  metadata["sourceRevision"], state["last_id"])
        if (checkpoint["pageCount"] != source.page_count or state["finished"] is not False
                or type(cursor) is not str or not 1 <= len(cursor) <= 1600
                or type(source_ref) is not str or re.fullmatch(r"[a-f0-9]{64}", source_ref) is None
                or type(revision) is not str or not 1 <= len(revision) <= 128
                or type(last_id) is not int or not 1 <= last_id <= MAX_SAFE_INTEGER
                or snapshot["nextArguments"].get("cursor") != cursor):
            _reject()
        last = m.AiBusinessEvidenceChunk.objects.filter(run_id=row.id, source_key=source.source_key)
        chunk = last.order_by("-sequence").values("sequence", "payload_json", "payload_digest").first()
        if (not chunk or chunk["sequence"] != source.page_count
                or len(chunk["payload_json"].encode("utf-8")) > 131072
                or digest(chunk["payload_json"]) != chunk["payload_digest"]):
            _reject()
        page = json.loads(chunk["payload_json"])
        items = page["items"]
        if (chunk["payload_json"] != canonical(page) or page["schemaVersion"] != SCHEMA_VERSION
                or page["sourceRef"] != source_ref or page["sourceRevision"] != revision
                or page["pagination"]["hasMore"] is not True
                or page["pagination"]["nextCursor"] != cursor
                or page["pagination"]["limit"] != 100
                or type(items) is not list or not 1 <= len(items) <= 100
                or page["pageEvidence"]["rowCount"] != len(items)
                or page["pageEvidence"]["sha256"] != row_digest(items)):
            _reject()
        tail = items[-1]["rowId"]
        if type(tail) is str and re.fullmatch(r"[1-9][0-9]{0,15}", tail): tail = int(tail)
        if type(tail) is not int or tail != last_id:
            _reject()
    except (KeyError, ValueError, TypeError, UnicodeError, AttributeError, RecursionError) as error:
        raise AiError("v3续读末块不是原检查点的真实页", "conflict", 409) from error
    arguments = {**query, "limit": 100, "cursor": cursor,
                 "expectedSourceRef": source_ref, "expectedRevision": revision,
                 "expectedLastId": last_id}
    prepared = Prepared(row.id, row.version, source.id, source.version, source.checkpoint_json,
                        actor, TOOLS[source.domain], canonical(arguments))
    check(prepared, principal)
    return prepared

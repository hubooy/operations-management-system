"""Test-only same-report v4 stream through the restricted 0075 reader role.

The development connection authenticates the existing report/v4 HMAC once;
persisted pages are fetched only through the supplied *separate* ai_reader
connection and 0075's one-page SECURITY DEFINER function. No report, file,
Agent or download authority is emitted.
"""
from __future__ import annotations

import hashlib

import psycopg
from django.conf import settings
from django.db import connection

from business_analysis import report_v4_promotion_stream_candidate as stream
from business_analysis.contracts import AnalysisContractError

from . import (business_v4_report_restricted_page_sql as sql,
    business_v4_report_stream_owning_candidate as owner,
    business_v4_seal_verify as seal_owner)
from .policy import AiError, digest, identifier


SCHEMA = "business-v4-report-restricted-page-owning-v1"
_LINK_QUERY = "SELECT public.ai_v4_read_report_source_link_ro(%s,%s,%s)"
_PAGE_QUERY = ("SELECT * FROM " + sql.PAGE.split("(", 1)[0] +
    "(%s,%s,%s,%s,%s,%s)")
_PAGE_FIELDS = ("sequence", "payloadJson", "payloadDigest", "sourceRef",
    "sourceRevision", "rowCount", "receiptResponseDigest",
    "auditResponseDigest", "requestArgumentsDigest", "toolName",
    "auditId", "invocationId")


def _need(ok, message="v4同报告受限页、封存或当前身份不一致"):
    if not ok:
        raise AiError(message, "conflict", 409)


def _closed(enabled):
    _need(enabled is True, "v4同报告受限页默认关闭")
    if (settings.DJANGO_ENVIRONMENT != "test" or
            settings.DJANGO_PROCESS_ROLE != "development"):
        raise AiError("v4同报告受限页仅供隔离测试", "access_denied", 403)


def _reader_identity(reader):
    """A caller-provided stub or the development ORM session cannot attest."""
    _need(connection.vendor == "postgresql"
        and isinstance(reader, psycopg.Connection)
        and reader is not connection.connection,
        "v4同报告受限页必须使用独立真实PG reader连接")
    observed = reader.execute("SELECT session_user,current_database(),"
        "inet_server_addr()::text,inet_server_port(),pg_backend_pid(),"
        "current_setting('transaction_read_only')"
        ).fetchone()
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        default_pid = cursor.fetchone()[0]
    _need(observed is not None and observed[:3] == (
        "teruisi_ai_reader", "test_teruisi_ai_rehearsal", "127.0.0.1")
        and type(observed[3]) is int and 55440 <= observed[3] <= 55999
        and observed[4] != default_pid and observed[5] == "on",
        "v4同报告受限页reader会话身份不符")
    return observed


def _linked(reader, report_id, run_id, report_context, v4_context):
    actor, parent, _, _, verified, _, _, _, _ = v4_context
    report, snapshot, evidence, fixed, _, _ = report_context
    current = reader.execute(_LINK_QUERY, [report_id, actor["email"],
        actor["version"]]).fetchone()
    _need(current is not None and type(current[0]) is dict)
    link = current[0]
    _need(link.get("reportId") == report.id == report_id
        and link.get("v2EvidenceRunId") == evidence.id
        and link.get("v2EvidenceVersion") == evidence.version
        and link.get("v2SealedDigest") == snapshot["sealedDigest"]
        and link.get("v4RunId") == parent.id == run_id
        and link.get("v4EvidenceVersion") == parent.version
        and link.get("v4PlanDigest") == parent.plan_digest
        and link.get("v4SealedDigest") == verified["sealedDigest"]
        and link.get("reportSnapshotDigest") == hashlib.sha256(
            report.snapshot_json.encode("utf-8")).hexdigest()
        and link.get("workflowInputDigest") == fixed["workflowInputDigest"]
        and link.get("creationTimeLinkPersisted") is True
        and all(link.get(flag) is False for flag in (
            "appHmacVerified", "authorityVerified",
            "reportGenerationSupported", "agentDispatchSupported",
            "rendererRegistered", "downloadSupported")))
    return link


def _pages(reader, report_id, parent, source, actor):
    """One bounded SQL result at a time; no ORM access to physical pages."""
    for sequence in range(1, source.page_count + 1):
        rows = reader.execute(_PAGE_QUERY,
            [report_id, parent.id, source.id, sequence, actor["email"],
             actor["version"]]).fetchall()
        _need(len(rows) == 1 and len(rows[0]) == len(_PAGE_FIELDS))
        page = dict(zip(_PAGE_FIELDS, rows[0]))
        _need(page["sequence"] == sequence
            and page["sourceRef"] == source.source_ref
            and page["sourceRevision"] == source.source_revision)
        page["auditSucceeded"] = True
        yield page


def inspect_candidate(report_id, run_id, principal, reader, *,
                      enabled=False, checkpoint=None):
    """Double-pass one linked window through the real restricted SQL role."""
    _closed(enabled)
    reader_identity = _reader_identity(reader)
    report_id, run_id = identifier(report_id), identifier(run_id)
    report_context = owner._report(report_id, principal)
    v4_context = owner._v4(run_id, principal)
    bridge = owner._unbound(report_context, v4_context)
    actor, parent, sources, directory, verified, body, _, _, seal_state = (
        v4_context)
    initial_link = _linked(reader, report_id, run_id, report_context,
        v4_context)
    _need(initial_link["sourceBindings"]["shop"] == bridge["shop"]
        and initial_link["sourceBindings"]["originalStartDate"] ==
            bridge["originalPeriod"]["startDate"]
        and initial_link["sourceBindings"]["originalEndDate"] ==
            bridge["originalPeriod"]["endDate"])
    receipts = []
    for window in ("current", "previous", "yearAgo"):
        chosen = next(item for item in bridge["promotionWindows"] if item[
            "window"] == window)
        source = next(item for item in sources if item.source_key == chosen[
            "sourceKey"])
        sealed = next(item for item in body["sources"] if item[
            "sourceKey"] == source.source_key)
        manifest = owner._source_manifest(bridge, source, sealed)

        def current(_bridge, _manifest):
            fresh = seal_owner._directory(run_id, principal)
            return (fresh[0] == actor and fresh[1].version == parent.version
                and fresh[3] == directory
                and [(s.id, s.version, s.source_ref, s.source_revision,
                      s.checkpoint_json) for s in fresh[2]] ==
                    [(s.id, s.version, s.source_ref, s.source_revision,
                      s.checkpoint_json) for s in sources]
                and _linked(reader, report_id, run_id,
                    owner._report(report_id, principal), v4_context) ==
                    initial_link)

        class Sink:
            def __init__(self):
                self.count = 0
                self.receipt = None

            def stage(self, source_key, index, raw):
                _need(self.receipt is None and index == self.count)
                self.count += 1

            def complete(self, receipt):
                _need(self.receipt is None and receipt["rowCount"] ==
                    self.count)
                self.receipt = receipt

            def abort(self):
                self.count = 0
                self.receipt = None

        sink = Sink()
        try:
            receipt = stream.stage_candidate(bridge, window, manifest,
                lambda: _pages(reader, report_id, parent, source, actor),
                sink, current, enabled=True, checkpoint=checkpoint)
            _need(sink.receipt == receipt)
            receipts.append(receipt)
        except (AnalysisContractError, KeyError, TypeError, ValueError,
                UnicodeError) as error:
            sink.abort()
            raise AiError("v4同报告受限页流不完整", "conflict", 409) from error
        except Exception:
            sink.abort()
            raise
    final = owner._v4(run_id, principal)
    _need(final[1].version == parent.version and final[3] == directory
        and final[4] == verified and final[8] == seal_state
        and _linked(reader, report_id, run_id,
            owner._report(report_id, principal), final) == initial_link)
    _need(_reader_identity(reader) == reader_identity)
    value = {"schemaVersion": SCHEMA,
        "status": "blocked_legacy_v4_report_authority",
        "reportId": report_id, "v4RunId": run_id,
        "v2SealedDigest": bridge["v2SealedDigest"],
        "v4SealedDigest": bridge["v4SealedDigest"],
        "sourceBindingsDigest": initial_link["sourceBindingsDigest"],
        "threeWindowStreamDigests": [item["resultDigest"] for item in receipts],
        "sqlCreationTimeLinkRechecked": True,
        "restrictedReaderPagesReplayedTwice": True,
        "currentLegacyApplicationHmacRechecked": True,
        "financeShopMappingAuthorityVerified": False,
        "reportCapableSealIssued": False,
        "agentCitationSupported": False,
        "rendererRegistered": False, "downloadSupported": False}
    return {**value, "resultDigest": digest(value)}

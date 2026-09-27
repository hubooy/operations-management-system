"""Default-closed material attestation for one parked market report."""
from __future__ import annotations

import json
from django.db import connection, transaction

from . import business_market_report_material as material_owner
from . import business_market_v2_parked_creation as parked
from . import models as m
from .policy import AiError, canonical, current_principal, digest, identifier

SCHEMA = "business-market-v2-material-attestation-candidate-v1"
ATTESTOR = "teruisi_ai_market_attestor"


def _need(ok, message="市场材料准入与停放报告不一致"):
    if not ok:
        raise AiError(message, "conflict", 409)


def _parked(report_id, principal):
    actor = current_principal(principal, admin=True)
    row = m.AiReportRun.objects.select_related("workflow").get(pk=report_id)
    snapshot = json.loads(row.snapshot_json)
    flow = row.workflow
    _need(row.owner_email == actor.email.lower()
        and snapshot.get("schemaVersion") == parked.SNAPSHOT_SCHEMA
        and snapshot.get("executionProfile") == parked.runtime.PROFILE
        and snapshot.get("reportId") == report_id
        and snapshot.get("marketMaterialReady") is False
        and snapshot.get("registered") is False
        and flow.status == "paused"
        and flow.error_code == parked.PAUSE_REASON
        and flow.allowed_tools_json == "[]"
        and flow.model_id == ""
        and flow.provider_round_count == 0
        and flow.tool_call_count == 0
        and not m.AiAgentJobs.objects.filter(workflow_run_id=flow.id).exists())
    return row, snapshot


def prepare_candidate(report_id, principal, *, checkpoint=None, limits=None):
    """Fully replay owning pages and exit the final fence before returning."""
    report_id = identifier(report_id, "reportId")
    row, snapshot = _parked(report_id, principal)
    root, selector = snapshot["sourceRoot"], snapshot["marketSelector"]
    with material_owner.prepare(root["sourceReportId"], selector, principal,
            checkpoint=checkpoint, limits=limits) as prepared:
        manifest, summary = prepared.manifest, prepared.summary
        _need(summary["selectedSealedSourcesFullyReplayed"] is True
            and summary["typedMarketRowsVerified"] is True
            and summary["authorityVerified"] is False
            and summary["reportId"] == root["sourceReportId"]
            and manifest["manifestDigest"] == summary["marketManifestDigest"]
            and manifest["algorithms"] == snapshot["marketAlgorithms"]
            and manifest["rankCurrentSourceKey"] == selector["rankCurrentSourceKey"]
            and manifest["rankBaselineKey"] == selector["rankBaselineKey"]
            and manifest["bands"] == selector["bands"])
        _need(len(prepared.tables) == 3)
        manifest_text, summary_text = canonical(manifest), canonical(summary)
        manifest_body_text = canonical({key: value for key, value in
            manifest.items() if key != "manifestDigest"})
        summary_body_text = canonical({key: value for key, value in
            summary.items() if key != "summaryDigest"})
        spec_texts = tuple(canonical(spec) for spec in manifest["tables"])
    _need(len(spec_texts) == 3)
    reread, new_snapshot = _parked(report_id, principal)
    _need(reread.id == row.id and digest(reread.snapshot_json) ==
        digest(row.snapshot_json) and new_snapshot == snapshot)
    result = {"schemaVersion": SCHEMA, "reportId": report_id,
        "sourceReportId": root["sourceReportId"],
        "sourceSnapshotDigest": root["sourceSnapshotDigest"],
        "sourceWorkflowInputDigest": root["sourceWorkflowInputDigest"],
        # The SQL sidecar hashes PostgreSQL's jsonb representation after an
        # exact equality check against the parked snapshot.  Do not present
        # Python-canonical hashes here as if they were the same encoding.
        "manifestDigest": manifest["manifestDigest"],
        "manifestJsonSha256": digest(manifest_text),
        "summaryDigest": digest(summary),
        "tableSpecDigests": [digest(value) for value in spec_texts],
        "tableViews": [spec["view"] for spec in manifest["tables"]],
        "rowCount": manifest["rowCount"],
        "authorityVerified": False, "agentDispatchSupported": False,
        "renderer8Supported": False, "materialPrepared": True,
        "registered": False}
    return {"candidate": result, "manifestJson": manifest_text,
        "manifestBodyJson": manifest_body_text, "summaryJson": summary_text,
        "summaryBodyJson": summary_body_text, "tableSpecJson": spec_texts}


def submit_candidate(report_id, principal, *, checkpoint=None, limits=None):
    """Independent attestor-only SQL submit; ordinary sessions fail closed."""
    value = prepare_candidate(report_id, principal, checkpoint=checkpoint,
        limits=limits)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT session_user")
            _need(cursor.fetchone()[0] == ATTESTOR,
                "市场材料仅能由独立证明进程提交")
            cursor.execute("SELECT public.ai_market_v2_attest_material("+
                ",".join(["%s"]*8)+")", [report_id, value["manifestJson"],
                value["manifestBodyJson"], value["summaryJson"],
                value["summaryBodyJson"], *value["tableSpecJson"]])
    return value["candidate"]

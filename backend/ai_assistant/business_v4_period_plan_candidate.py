"""Owner-bound append-only creation of a non-authoritative v4 date envelope.

No route, scheduler, Agent, sealer or renderer imports this service. The
database independently checks the current run, latest attempt, all four
source identities and resolved dates before recording the candidate.
"""
from __future__ import annotations

import json

from django.db import DatabaseError, connection

from business_analysis import period_bound_plan_v1
from business_analysis.contracts import AnalysisContractError

from . import business_v4_validation as validation, models as m
from .policy import AiError, canonical, digest, identifier, mutation


SCHEMA = "business-v4-period-plan-persisted-candidate-v1"


def _reject(message="v4日期候选与当前来源或验证尝试不一致"):
    raise AiError(message, "conflict", 409)


def _prepared(run_id, principal):
    actor, parent, sources, directory_digest = validation._directory(
        identifier(run_id), principal)
    attempt = (m.AiBusinessV4ValidationAttempt.objects.filter(run_id=parent.id)
        .order_by("-created_at", "-id").first())
    if (attempt is None or attempt.run_version != parent.version
            or attempt.plan_digest != parent.plan_digest
            or attempt.directory_digest != directory_digest
            or attempt.actor_email != actor["email"]
            or attempt.actor_version != actor["version"]):
        _reject("v4日期候选须绑定最新有效验证尝试")
    try:
        plan = json.loads(parent.plan_json)
        value = period_bound_plan_v1.prepare_candidate(plan)
        raw = canonical(value)
    except (AnalysisContractError, KeyError, TypeError, ValueError,
            UnicodeError, RecursionError) as error:
        raise AiError("v4三期日期无法从当前持久计划严格重建", "conflict", 409) from error
    if (value["basePlanDigest"] != plan["planDigest"]
            or len(sources) != 4 or len(raw.encode("utf-8")) > 65536):
        _reject("v4三期日期与当前物理来源数量不一致")
    return actor, parent, attempt, directory_digest, raw


def create_candidate(run_id, principal):
    """Write once under the current admin/AI authority; same bytes are idempotent."""
    actor, parent, attempt, directory_digest, raw = _prepared(run_id, principal)
    with mutation(principal):
        live = m.AiBusinessV4Run.objects.select_for_update().get(pk=parent.id)
        current_actor, current, current_attempt, current_directory, current_raw = (
            _prepared(live.id, principal))
        if (current_actor != actor or current.id != parent.id
                or current.version != parent.version
                or current.plan_digest != parent.plan_digest
                or current_attempt.id != attempt.id
                or current_directory != directory_digest or current_raw != raw):
            _reject("v4日期候选写入前账号、计划或最新来源版本变化")
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT envelope_digest,source_root,created_at "
                    "FROM public.ai_v4_record_period_plan_candidate("
                    "%s,%s,%s,%s,%s,%s)",
                    [live.id, attempt.id, actor["email"], actor["version"],
                     raw, digest(raw)])
                rows = cursor.fetchmany(2)
        except DatabaseError as error:
            raise AiError("v4日期候选写入未取得确定回执", "conflict", 409) from error
        if (len(rows) != 1 or rows[0][0] != digest(raw)
                or type(rows[0][1]) is not str or len(rows[0][1]) != 64
                or rows[0][2] is None):
            _reject("v4日期候选回执摘要不一致")
        result = {"schemaVersion": SCHEMA, "runId": live.id,
            "attemptId": attempt.id, "basePlanDigest": live.plan_digest,
            "directoryDigest": directory_digest,
            "envelopeDigest": rows[0][0], "sourceRoot": rows[0][1],
            "candidateOnly": True, "sourceAuthorityVerified": False,
            "observedDailyCoverageVerified": False,
            "zeroDayCertificationVerified": False,
            "agentCitationSupported": False,
            "registeredRenderer": False}
    return result

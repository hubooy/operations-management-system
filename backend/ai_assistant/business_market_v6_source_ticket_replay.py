"""Test-only owning replay of a 0079 market page pointer.

The caller must separately obtain a protected OUTCOME from the dedicated SQL
role. This reader does not authenticate that handoff or persist an Agent read.
"""
import json

from django.conf import settings
from django.db import DatabaseError

from access_control.models import AppUser
from business_analysis.contracts import AnalysisContractError, canonical

from . import business_market_v6_source_ticket_contract as contract
from . import business_market_composite_export as composite
from . import models as m
from .policy import AiError, current_principal


def _closed():
    if (getattr(settings, "AI_MARKET_V6_SOURCE_TICKET_REPLAY_ENABLED",
            False) is not True or settings.DJANGO_ENVIRONMENT != "test"
            or settings.DJANGO_PROCESS_ROLE != "ai_reader"):
        raise AiError("市场 v6 来源页重读尚未启用",
            "market_v6_source_ticket_replay_disabled", 409)


def _binding(ticket, principal):
    actor = current_principal(principal, admin=True)
    if actor.email.lower() != ticket["ownerEmail"]:
        raise AnalysisContractError("来源票据管理员不匹配")
    user = AppUser.objects.filter(email=ticket["ownerEmail"],
        status="active", role_id="admin", scope__isnull=True).values(
        "email", "version").first()
    row = m.AiReportRun.objects.filter(pk=ticket["parkedReportId"]).values(
        "id", "owner_email", "scope_json", "snapshot_json").first()
    if (user is None or user["version"] != ticket["ownerVersion"]
            or row is None or row["owner_email"] != ticket["ownerEmail"]
            or row["scope_json"] != "null"):
        raise AnalysisContractError("来源页账号或停泊报告已变化")
    value = json.loads(row["snapshot_json"])
    if (canonical(value) != row["snapshot_json"]
            or value.get("executionProfile") !=
                "business-agent-screening-promotion-market-reference-v2"
            or value.get("sourceRoot", {}).get("sourceReportId") !=
                ticket["sealedSourceReportId"]
            or value.get("sourceRoot", {}).get("evidenceRunId") !=
                ticket["evidenceRunId"]
            or value.get("sourceRoot", {}).get("evidenceVersion") !=
                ticket["evidenceVersion"]
            or value.get("sourceRoot", {}).get("sealedDigest") !=
                ticket["sealedDigest"]
            or type(value.get("marketSelector")) is not dict
            or value["marketSelector"].get("currentObservationDate") !=
                ticket["currentObservationDate"]
            or value["marketSelector"].get("baselineObservationDate") !=
                ticket["baselineObservationDate"]):
        raise AnalysisContractError("来源页封存选择已变化")
    return (user["version"], row["snapshot_json"],
        value["marketSelector"])


def replay_candidate(raw_ticket, principal):
    """Fully replay one bounded rank page twice; never claim ticket provenance."""
    _closed()
    ticket = contract.validate(raw_ticket)
    try:
        before = _binding(ticket, principal)
        selector = before[2]
        def owning_page():
            material = composite.prepare(ticket["sealedSourceReportId"],
                selector["rankCurrentSourceKey"], selector["rankBaselineKey"],
                ticket["currentObservationDate"],
                ticket["baselineObservationDate"], principal,
                bands=selector["bands"])
            return contract.bind_replayed_page(ticket, material.manifest,
                material.ndjson_pages(ticket["view"]))
        one = owning_page()
        two = owning_page()
        if one != two or _binding(ticket, principal) != before:
            raise AnalysisContractError("来源页双遍复核变化")
        return {**one, "pageBytesVerified": True,
            "samePageOnSecondReplay": True,
            "protectedTicketProvenanceVerified": False,
            "shopSalesStatus": "unknown_not_supplied",
            "b2bStatus": "unknown_not_supplied",
            "yearAgoStatus": "unknown_not_supplied"}
    except (AiError, AnalysisContractError, DatabaseError, KeyError,
            TypeError, ValueError, UnicodeError) as error:
        raise AiError("市场 v6 封存来源页双遍重读失败",
            "market_v6_source_ticket_replay_unverified", 409) from error

from copy import deepcopy
import hashlib
from unittest import TestCase

from business_analysis.contracts import AnalysisContractError, canonical, digest

from . import business_market_v6_source_ticket_contract as ticket


def valid_ticket():
    value = {
        "schemaVersion": ticket.SCHEMA,
        "ticketId": "market-v6-page-" + "a"*48,
        "reportId": "market-v6-report-" + "a"*48,
        "workflowId": "market-v6-flow-" + "a"*48,
        "jobId": "market-v6-job-" + "a"*48,
        "role": "market_b2b",
        "ownerEmail": "admin@example.invalid", "ownerVersion": 3,
        "sourceExecutionReportId": "source-execution",
        "admittedReportId": "admitted-report",
        "parkedReportId": "parked-report",
        "sealedSourceReportId": "source-report",
        "evidenceRunId": "evidence-run", "evidenceVersion": 7,
        "sealedDigest": "a"*64,
        "topologySnapshotDigest": "b"*64,
        "sourcePlanDigest": "c"*64, "selectorDigest": "d"*64,
        "admissionDigest": "5"*64, "marketContextDigest": "6"*64,
        "marketManifestDigest": "e"*64,
        "view": "rank_entry_exit", "pageIndex": 1,
        "maxPageBytes": 38_000,
        "rowCount": 2, "pageCount": 2,
        "tableBindingDigest": "f"*64, "ndjsonSha256": "1"*64,
        "currentSourceKey": "source-current",
        "baselineSourceKey": "source-baseline",
        "currentObservationDate": "2026-09-25",
        "baselineObservationDate": "2026-09-24",
        "marketStatus": "bound_selected_top_sample",
        "shopStatus": "unknown_not_supplied",
        "shopSalesStatus": "unknown_not_supplied",
        "b2bStatus": "unknown_not_supplied",
        "yearAgoStatus": "unknown_not_supplied",
        "providerCallsAllowed": False, "toolDispatchAllowed": False,
        "agentReadPersisted": False, "numericCitationAllowed": False,
        "reportPublishAuthorized": False, "pageBytesVerified": False,
    }
    value["ticketDigest"] = digest(value)
    return value


class MarketV6SourceTicketContractTests(TestCase):
    def test_accepts_only_narrow_market_pointer(self):
        value = valid_ticket()
        self.assertEqual(ticket.validate(value), value)
        self.assertEqual(value["b2bStatus"], "unknown_not_supplied")

    def test_rejects_cross_report_owner_source_or_page_drift(self):
        for field, changed in (("reportId", "another-report"),
                ("ownerVersion", 4), ("sealedSourceReportId", "other-source"),
                ("currentSourceKey", "source-baseline"),
                ("marketManifestDigest", "2"*64), ("pageIndex", 3),
                ("tableBindingDigest", "3"*64),
                ("currentObservationDate", "2026-09-20")):
            value = valid_ticket()
            value[field] = changed
            with self.subTest(field=field), self.assertRaises(AnalysisContractError):
                ticket.validate(value)

    def test_does_not_invent_shop_b2b_year_ago_or_agent_authority(self):
        for field, changed in (("shopStatus", "bound"),
                ("shopSalesStatus", "bound"), ("b2bStatus", "zero"),
                ("yearAgoStatus", "not_in_top"),
                ("agentReadPersisted", True),
                ("numericCitationAllowed", True),
                ("providerCallsAllowed", True)):
            value = valid_ticket()
            value[field] = changed
            value["ticketDigest"] = digest({key: item for key, item in
                value.items() if key != "ticketDigest"})
            with self.subTest(field=field), self.assertRaises(AnalysisContractError):
                ticket.validate(value)

    def test_owning_replay_must_match_ticket_exactly(self):
        value = valid_ticket()
        pages = [(canonical({"rowIndex": index, "skuId": str(index)})+"\n"
            ).encode("utf-8") for index in range(2)]
        value["ndjsonSha256"] = hashlib.sha256(b"".join(pages)).hexdigest()
        spec = {"view": "rank_entry_exit", "rowCount": 2,
            "pageCount": 2, "sourceTableDigest": value["tableBindingDigest"],
            "ndjsonBytes": sum(map(len, pages)),
            "ndjsonSha256": value["ndjsonSha256"]}
        manifest = {"reportBinding": {"reportId": value["sealedSourceReportId"]},
            "rankCurrentSourceKey": value["currentSourceKey"],
            "rankBaselineKey": value["baselineSourceKey"],
            "observationDates": {
                "current": value["currentObservationDate"],
                "baseline": value["baselineObservationDate"]},
            "tables": [{"view": "price_band_summary"},
                {"view": "price_band_members"}, spec]}
        manifest["manifestDigest"] = digest(manifest)
        value["marketManifestDigest"] = manifest["manifestDigest"]
        value["ticketDigest"] = digest({key: item for key, item in
            value.items() if key != "ticketDigest"})
        replay = ticket.bind_replayed_page(value, manifest, pages)
        self.assertFalse(replay["agentReadPersisted"])
        self.assertFalse(replay["pageBytesVerified"])
        self.assertEqual(replay["rows"], [{"rowIndex": 1, "skuId": "1"}])
        wrong = deepcopy(pages)
        wrong[1] = (canonical({"rowIndex": 1, "skuId": "tampered"})+"\n"
            ).encode("utf-8")
        with self.assertRaises(AnalysisContractError):
            ticket.bind_replayed_page(value, manifest, wrong)
        with self.assertRaises(AnalysisContractError):
            ticket.bind_replayed_page(value, manifest, reversed(pages))
        with self.assertRaises(AnalysisContractError):
            ticket.bind_replayed_page(value, manifest, [b"\xff\n", pages[1]])

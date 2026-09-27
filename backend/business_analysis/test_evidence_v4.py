"""No-DB, no-GB-allocation coverage of prospective v4 capacity arithmetic."""
from copy import deepcopy
from unittest import TestCase

from . import evidence_v4
from .contracts import AnalysisContractError, digest


ROWS = 575_095


def sources(windows=("current",)):
    days = {"startDate": "2026-08-20", "endDate": "2026-09-18"}
    daily = [{"key": "promotion-" + window, "domain": "netshop", "query": {
        "platform": "京东", "shop": "测试店", "dataset": "promotion", **days,
        "window": window}} for window in windows]
    finance = {"key": "finance-months", "domain": "finance", "query": {
        "months": ["2026-08", "2026-09"], "scope": {"scope_key": "shop:测试店",
            "scope_type": "shop", "scope_name": "测试店", "group_name": "京东组"},
        "analysisPeriod": days}}
    analysis_request = {"schemaVersion": "business-analysis-request-v1",
        "question": "推广与财报自然月背景", "requestedDimensions": ["shop", "keyword"],
        "requestedWindows": list(windows)}
    return [*daily, finance], analysis_request


def measurements(windows=("current",), *, width=1000, overhead=2048):
    return [{"sourceKey": "promotion-" + window, "measuredRowCount": ROWS,
        "maxRowUtf8Bytes": width, "pageEnvelopeUtf8Bytes": overhead,
        "sourceRevisionHint": "1:2", "sampleRows": [{"name": "志高"}]}
        for window in windows] + [{"sourceKey": "finance-months", "measuredRowCount": 0,
            "maxRowUtf8Bytes": 0, "pageEnvelopeUtf8Bytes": 1000,
            "sourceRevisionHint": "0:" + "a" * 64}]


class EvidenceV4CapacityTests(TestCase):
    def plan(self, windows=("current",), **options):
        planned, request = sources(windows)
        return evidence_v4.build_plan(client_request_id="v4-reference-575095",
            sources=planned, measurements=measurements(windows, **options),
            analysis_request=request)

    def test_one_and_three_windows_use_exact_page_math_without_allocating_rows(self):
        one = self.plan()
        current = next(item for item in one["sourcePlans"] if item["sourceKey"] == "promotion-current")
        self.assertEqual(current["measuredRowCount"], 575_095)
        self.assertEqual(current["conservativeRowsPerPage"], 100)
        self.assertEqual(current["estimatedPageCount"], 5_751)
        self.assertEqual(one["estimatedTotalPages"], 5_752)  # one empty finance page
        self.assertTrue(one["runCapacitySupported"])
        self.assertFalse(one["measurementAuthorityVerified"])
        self.assertFalse(one["reportGenerationSupported"])
        three = self.plan(("current", "previous", "yearAgo"))
        self.assertEqual(three["estimatedTotalPages"], 17_254)
        self.assertEqual(sum(item["estimatedPageCount"] for item in three["sourcePlans"]
            if item["domain"] != "finance"), 17_253)
        self.assertTrue(three["runCapacitySupported"])
        self.assertFalse(three["crossDomainSnapshotAtomic"])
        self.assertFalse(three["financeDailyProrationAllowed"])

    def test_measured_width_can_make_reference_unsupported_without_truncation(self):
        wide = self.plan(width=4000)
        promotion = next(item for item in wide["sourcePlans"] if item["domain"] == "netshop")
        self.assertEqual(promotion["conservativeRowsPerPage"], 32)
        self.assertEqual(promotion["estimatedPageCount"], 17_972)
        self.assertFalse(promotion["sourceCapacitySupported"])
        self.assertIn("source_page_cap_exceeded", promotion["unsupportedReasons"])
        self.assertIn("source_byte_cap_exceeded", promotion["unsupportedReasons"])
        self.assertFalse(wide["runCapacitySupported"])
        three_wide = self.plan(("current", "previous", "yearAgo"), width=6000)
        self.assertFalse(three_wide["runCapacitySupported"])
        self.assertIn("run_page_cap_exceeded_or_unknown", three_wide["unsupportedReasons"])
        self.assertIn("run_byte_cap_exceeded_or_unknown", three_wide["unsupportedReasons"])
        impossible = self.plan(width=131_072)
        item = next(source for source in impossible["sourcePlans"] if source["domain"] == "netshop")
        self.assertIsNone(item["estimatedPageCount"])
        self.assertIn("single_row_or_page_envelope_exceeds_page_bytes", item["unsupportedReasons"])

    def test_finance_owning_row_cap_is_part_of_v4_capacity(self):
        planned, request = sources()
        measured = measurements()
        finance = measured[-1]
        finance.update(measuredRowCount=100_000, maxRowUtf8Bytes=100,
            pageEnvelopeUtf8Bytes=2048)
        supported = evidence_v4.build_plan(client_request_id="finance-boundary",
            sources=planned, measurements=measured, analysis_request=request)
        source = next(item for item in supported["sourcePlans"] if item["domain"] == "finance")
        self.assertTrue(source["sourceCapacitySupported"])
        self.assertTrue(supported["runCapacitySupported"])
        finance["measuredRowCount"] = 100_001
        unsupported = evidence_v4.build_plan(client_request_id="finance-boundary",
            sources=planned, measurements=measured, analysis_request=request)
        source = next(item for item in unsupported["sourcePlans"] if item["domain"] == "finance")
        self.assertFalse(source["sourceCapacitySupported"])
        self.assertIn("finance_owning_row_cap_exceeded", source["unsupportedReasons"])
        self.assertFalse(unsupported["runCapacitySupported"])

    def test_canonical_utf8_and_measurement_identity_fail_closed(self):
        chinese = evidence_v4.row_utf8_bytes({"name": "志高"})
        self.assertGreater(chinese, len('{"name":"志高"}'))
        planned, request = sources()
        measured = measurements()
        measured[0]["maxRowUtf8Bytes"] = chinese - 1
        with self.assertRaises(AnalysisContractError):
            evidence_v4.build_plan(client_request_id="v4-reference-575095",
                sources=planned, measurements=measured, analysis_request=request)
        measured = measurements()
        measured[1]["sourceKey"] = measured[0]["sourceKey"]
        with self.assertRaises(AnalysisContractError):
            evidence_v4.build_plan(client_request_id="v4-reference-575095",
                sources=planned, measurements=measured, analysis_request=request)
        with self.assertRaises(AnalysisContractError): evidence_v4.row_utf8_bytes({"bad": float("nan")})

    def test_plan_and_versioned_chunk_receipt_identities_are_deterministic(self):
        planned, request = sources()
        measured = measurements()
        one = evidence_v4.build_plan(client_request_id="same", sources=planned,
            measurements=measured, analysis_request=request)
        reordered = evidence_v4.build_plan(client_request_id="same", sources=list(reversed(planned)),
            measurements=list(reversed(measured)), analysis_request=request)
        self.assertEqual(one, reordered)
        changed = deepcopy(measured); changed[0]["sourceRevisionHint"] = "1:3"
        different = evidence_v4.build_plan(client_request_id="same", sources=planned,
            measurements=changed, analysis_request=request)
        self.assertNotEqual(one["planDigest"], different["planDigest"])
        source = next(item for item in one["sourcePlans"] if item["domain"] == "netshop")
        chunk = evidence_v4.chunk_identity(run_identity_digest=one["runIdentityDigest"],
            source_identity_digest=source["sourceIdentityDigest"], sequence=1,
            source_ref="a" * 64, source_revision="1:2", payload_digest="b" * 64)
        receipt = evidence_v4.receipt_identity(chunk=chunk, audit_id="audit-1",
            invocation_id="invocation-1", actor_email="admin@example.test",
            tool_name="get_business_source_page", response_digest="b" * 64)
        self.assertEqual(receipt["schemaVersion"], evidence_v4.RECEIPT_IDENTITY_SCHEMA)
        self.assertFalse(receipt["authorityVerified"])
        self.assertNotEqual(chunk["chunkIdentityDigest"], evidence_v4.chunk_identity(
            run_identity_digest=one["runIdentityDigest"],
            source_identity_digest=source["sourceIdentityDigest"], sequence=1,
            source_ref="a" * 64, source_revision="1:3", payload_digest="b" * 64)["chunkIdentityDigest"])
        with self.assertRaises(AnalysisContractError):
            evidence_v4.receipt_identity(chunk=chunk, audit_id="audit-1",
                invocation_id="invocation-1", actor_email="admin@example.test",
                tool_name="get_business_source_page", response_digest="c" * 64)

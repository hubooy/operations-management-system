"""Pure checks for complete finance pages and compact resumable state."""
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

from . import finance_collection_state as state, finance_source
from .contracts import AnalysisContractError, canonical, digest
from .test_finance_source import fixture, row


def sources(rows=None, *, include_september=True, include_october=False):
    args = fixture(rows)
    months = ["2026-08", "2026-09"] + (["2026-10"] if include_october else [])
    args["query"]["months"] = months
    if include_september:
        args["months"].append({"month": "2026-09", "batch_id": "september", "status": "completed"})
        args["batches"].append({"id": "september", "status": "completed", "content_hash": "d" * 64,
                                "raw_file_hash": "e" * 64, "published_state_token": "f" * 64})
    period = {"startDate": "2026-08-20", "endDate": "2026-09-18"}
    source = finance_source.build(**args, analysis_period=period)
    manifest = source.manifest
    data = []
    offset = 0
    while True:
        page = source.page(offset)
        data.extend(page["rows"])
        offset = page["pagination"]["nextOffset"]
        if offset is None: break
    query = {**manifest["query"], "analysisPeriod": period}
    publication = {"months": manifest["months"], "batches": manifest["batches"],
                   "missingMonths": [month for month in query["months"] if month not in {item["month"] for item in manifest["months"]}]}
    return source, query, publication, data


def owned_page(query, publication, rows, *, offset, total, source_ref=None, revision="7:" + "a" * 64):
    source_ref = source_ref or "b" * 64
    end = offset + len(rows)
    value = {"schemaVersion": state.PAGE_SCHEMA, "sourceRef": source_ref,
             "sourceRevision": revision, "query": query,
             "periodAlignment": finance_source._period({key: query[key] for key in ("months", "scope")}, query["analysisPeriod"]),
             "publication": publication, "rows": rows,
             "pageEvidence": {"rowCount": len(rows), "sha256": digest(rows)},
             "pagination": {"offset": offset, "returned": len(rows), "total": total,
                            "nextOffset": end if end < total else None,
                            "nextLastId": rows[-1]["id"] if end < total else None},
             "sourceAuthorityVerified": False, "persistentEvidenceVerified": False}
    return {**value, "pageDigest": digest(value)}


class FinanceCollectionStateTests(TestCase):
    def test_complete_two_pages_keep_zero_null_month_gaps_and_row_chain(self):
        august_zero = row(1); august_zero["amount_cents"] = 0
        august_rate = row(2, subject_name="大毛利率"); august_rate.update(metric_key="gross_margin",
            value_type="rate", amount_cents=None, rate_bps=6000, source_row_count=2)
        kingdee = row(3, subject_name="金蝶销售费用"); kingdee.update(section="kingdee", is_total=True,
            metric_key="selling_expense_total", amount_cents=700)
        september_null = row(4, subject_name="九月实际销售"); september_null.update(month="2026-09", amount_cents=None)
        original, query, publication, records = sources(
            [august_zero, august_rate, kingdee, september_null], include_october=True)
        first = owned_page(query, publication, records[:2], offset=0, total=4)
        checkpoint = state.consume(None, first, trusted_query=query)
        self.assertFalse(checkpoint["finished"])
        self.assertFalse(checkpoint["persistentEvidenceVerified"])
        self.assertEqual(state.next_arguments(checkpoint, trusted_query=query), {
            "query": query, "offset": 2, "afterId": 2, "expectedSourceRef": "b"*64,
            "expectedRevision": "7:"+"a"*64})
        with self.assertRaises(AnalysisContractError): state.result(checkpoint, trusted_query=query)
        last = owned_page(query, publication, records[2:], offset=2, total=4)
        finished = state.consume(checkpoint, last, trusted_query=query)
        result = state.result(finished, trusted_query=query)
        self.assertEqual(result["rowCount"], 4)
        self.assertEqual(result["pageCount"], 2)
        self.assertEqual(result["rowChainDigest"], original.manifest["rowChainDigest"])
        self.assertEqual(result["coverage"][0]["metrics"]["net_sales"],
                         {"status": "present", "value": 0, "unit": "CNY_cent"})
        self.assertEqual(result["coverage"][1]["metrics"]["net_sales"]["status"], "missing_value")
        self.assertEqual(result["coverage"][2]["metrics"]["net_sales"]["status"], "missing_month")
        self.assertEqual(result["coverage"][0]["metrics"]["gross_margin"]["value"], 6000)
        self.assertEqual(result["coverage"][0]["metrics"]["selling_expense_total"]["status"], "missing_subject")
        self.assertEqual(result["coverage"][0]["kingdeeRows"], 1)
        self.assertEqual(result["coverage"][0]["totalRows"], 1)
        self.assertEqual(result["coverage"][0]["mergedRows"], 1)
        self.assertFalse(result["persistentEvidenceVerified"])
        self.assertFalse(result["dailyProrationAllowed"])
        self.assertFalse(result["inferSkuProfit"])
        with self.assertRaises(AnalysisContractError): state.consume(finished, last, trusted_query=query)

    def test_first_empty_page_preserves_missing_month_and_no_zero_claim(self):
        original, query, publication, rows = sources([], include_september=False, include_october=True)
        self.assertEqual(rows, [])
        checkpoint = state.consume(None, owned_page(query, publication, [], offset=0, total=0), trusted_query=query)
        result = state.result(checkpoint, trusted_query=query)
        self.assertEqual(result["coverage"][0]["metrics"]["net_sales"]["status"], "missing_subject")
        self.assertEqual(result["coverage"][1]["metrics"]["net_sales"]["status"], "missing_month")
        self.assertEqual(result["coverage"][2]["metrics"]["net_sales"]["status"], "missing_month")
        self.assertEqual(result["rowChainDigest"], original.manifest["rowChainDigest"])

    def test_duplicate_summary_metric_is_ambiguous_not_summed(self):
        one = row(1)
        two = row(2, subject_name="另一实际销售")
        source, query, publication, rows = sources([one, two])
        result = state.result(state.consume(None, owned_page(query, publication, rows, offset=0, total=2),
                         trusted_query=query), trusted_query=query)
        self.assertEqual(result["coverage"][0]["metrics"]["net_sales"]["status"], "ambiguous_subject")
        self.assertIsNone(result["coverage"][0]["metrics"]["net_sales"]["value"])

    def test_tampered_page_fields_row_hash_and_stale_source_fail(self):
        _, query, publication, rows = sources([row(1), row(2, subject_name="第二行")])
        first = owned_page(query, publication, rows[:1], offset=0, total=2)
        checkpoint = state.consume(None, first, trusted_query=query)
        good = owned_page(query, publication, rows[1:], offset=1, total=2)
        bads = []
        altered = deepcopy(good); altered["sourceRef"] = "c"*64; bads.append(altered)
        altered = deepcopy(good); altered["sourceRevision"] = "8:"+"a"*64; bads.append(altered)
        altered = deepcopy(good); altered["publication"]["months"][0]["batch_id"] = "missing"; bads.append(altered)
        altered = deepcopy(good); altered["pagination"]["offset"] = 0; bads.append(altered)
        altered = deepcopy(good); altered["rows"][0]["rowId"] = "0"*64; bads.append(altered)
        altered = deepcopy(good); altered["rows"][0]["id"] = 1; bads.append(altered)
        altered = deepcopy(good); altered["sourceAuthorityVerified"] = True; bads.append(altered)
        altered = deepcopy(good); altered["extra"] = 1; bads.append(altered)
        for bad in bads:
            bad["pageEvidence"] = {"rowCount": len(bad["rows"]), "sha256": digest(bad["rows"])}
            bad["pageDigest"] = digest({key: item for key, item in bad.items() if key != "pageDigest"})
            with self.subTest(bad=bad), self.assertRaises(AnalysisContractError):
                state.consume(checkpoint, bad, trusted_query=query)

    def test_checkpoint_is_compact_bound_to_query_and_not_a_trust_anchor(self):
        _, query, publication, rows = sources([row(1), row(2, subject_name="第二行")])
        page = owned_page(query, publication, rows[:1], offset=0, total=2)
        checkpoint = state.consume(None, page, trusted_query=query)
        self.assertLessEqual(len(canonical(checkpoint).encode("utf-8")), state.CHECKPOINT_BYTES)
        altered = deepcopy(checkpoint); altered["rowsRead"] = 2
        with self.assertRaises(AnalysisContractError):
            state.next_arguments(altered, trusted_query=query)
        other = deepcopy(query); other["scope"]["group_name"] = "另一组"
        with self.assertRaises(AnalysisContractError):
            state.next_arguments(checkpoint, trusted_query=other)
        copied = deepcopy(checkpoint); copied["monthCounts"]["2026-08"]["scopeRows"] = 999
        copied["checkpointDigest"] = digest({key: item for key, item in copied.items() if key != "checkpointDigest"})
        with self.assertRaises(AnalysisContractError):
            state.next_arguments(copied, trusted_query=query)

    def test_capacity_and_page_count_fail_closed(self):
        _, query, publication, rows = sources([row(1), row(2, subject_name="第二行")])
        first = owned_page(query, publication, rows[:1], offset=0, total=2)
        second = owned_page(query, publication, rows[1:], offset=1, total=2)
        with patch.object(state, "PAGE_BYTES", 100), self.assertRaises(AnalysisContractError):
            state.consume(None, first, trusted_query=query)
        checkpoint = state.consume(None, first, trusted_query=query)
        with patch.object(state, "MAX_DATA_PAGES", 1), self.assertRaises(AnalysisContractError):
            state.consume(checkpoint, second, trusted_query=query)
        with patch.object(state, "TOTAL_BYTES", len(canonical(first).encode("utf-8")) + state.FINAL_HEADER_RESERVE), self.assertRaises(AnalysisContractError):
            state.consume(checkpoint, second, trusted_query=query)

    def test_full_24_month_gap_checkpoint_stays_bounded(self):
        _, query, publication, _ = sources([], include_september=False)
        query["months"] = [f"{year}-{month:02d}" for year in (2025, 2026) for month in range(1, 13)]
        publication["missingMonths"] = [month for month in query["months"] if month != "2026-08"]
        page = owned_page(query, publication, [], offset=0, total=0)
        checkpoint = state.consume(None, page, trusted_query=query)
        self.assertLessEqual(len(canonical(checkpoint).encode("utf-8")), state.CHECKPOINT_BYTES)
        self.assertEqual(len(state.result(checkpoint, trusted_query=query)["coverage"]), 24)

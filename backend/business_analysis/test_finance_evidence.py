"""Pure finance evidence contract checks; no Django or database access."""
from unittest import TestCase
from unittest.mock import patch

from . import finance_evidence, finance_source
from .contracts import AnalysisContractError, canonical, digest
from .test_finance_source import fixture, row


def bound_source(rows=None, *, months=None, analysis_period=None):
    args = fixture(rows)
    if months is not None:
        args["query"]["months"] = months
    source = finance_source.build(**args, analysis_period=analysis_period)
    manifest = source.manifest
    binding = {"schemaVersion": "finance-owning-memory-binding-v1",
               "actor": {"email": "finance-admin@example.test", "role": "admin",
                         "status": "active", "scope": None, "version": 4},
               "sourceDigest": manifest["sourceDigest"], "revision": manifest["revision"],
               "publicationDigest": digest([manifest["months"], manifest["batches"]]),
               "persistentEvidenceVerified": False}
    return source, binding


class FinanceEvidenceTests(TestCase):
    def test_full_month_batch_and_null_zero_evidence_are_preserved(self):
        zero = row(1); zero["amount_cents"] = 0
        absent = row(2, subject_name="净成本"); absent.update(metric_key="net_cost", amount_cents=None)
        detail = row(3, subject_name="金蝶明细"); detail.update(section="kingdee", is_total=False)
        total = row(4, subject_name="金蝶合计"); total.update(section="kingdee", is_total=True)
        source, binding = bound_source([zero, absent, detail, total],
            months=["2026-08", "2026-09"],
            analysis_period={"startDate": "2026-08-03", "endDate": "2026-09-01"})
        proof = finance_evidence.prepare(source, binding)
        header = proof.manifest
        self.assertEqual(header["schemaVersion"], finance_evidence.SCHEMA)
        self.assertEqual(header["sourceManifest"], source.manifest)
        self.assertEqual(header["sourceManifest"]["coverage"][0]["metrics"]["net_sales"]["value"], 0)
        self.assertEqual(header["sourceManifest"]["coverage"][0]["metrics"]["net_cost"]["status"], "missing_value")
        self.assertEqual(header["sourceManifest"]["coverage"][1]["metrics"]["net_sales"]["status"], "missing_month")
        self.assertEqual(header["sourceManifest"]["periodAlignment"]["alignment"], "different_or_partial_months")
        self.assertEqual([r["is_total"] for r in proof.page()["rows"]], [False, False, False, True])
        self.assertEqual([r["section"] for r in proof.page()["rows"]], ["summary", "summary", "kingdee", "kingdee"])
        self.assertFalse(any(header[key] for key in ("sourceAuthorityVerified", "persistentEvidenceVerified",
                                                    "businessCoverageVerified", "agentReadVerified")))
        self.assertEqual(header["numericPolicy"], {"nullIsZero": False, "sumTotalsAndDetails": False,
            "sumSourceRates": False, "inferSkuProfit": False, "proratePartialMonths": False})

    def test_rate_is_source_scalar_with_merge_warning_not_calculated_result(self):
        rate = row(1, subject_name="大毛利率"); rate.update(metric_key="gross_margin", value_type="rate",
            amount_cents=None, rate_bps=6000, source_row_count=2)
        source, binding = bound_source([rate])
        proof = finance_evidence.prepare(source, binding)
        self.assertEqual(proof.page()["rows"][0]["rate_bps"], 6000)
        self.assertEqual(proof.page()["rows"][0]["source_row_count"], 2)
        self.assertEqual(proof.manifest["sourceManifest"]["coverage"][0]["metrics"]["gross_margin"]["value"], 6000)
        self.assertFalse(proof.manifest["numericPolicy"]["sumSourceRates"])
        self.assertIn("source_row_count", " ".join(proof.manifest["sourceManifest"]["limitations"]))

    def test_full_prefix_pages_chain_and_copies(self):
        records = [dict(row(i, subject_name=f"科目{i}"), raw_value="中文" * 800) for i in range(1, 105)]
        source, binding = bound_source(records)
        proof = finance_evidence.prepare(source, binding)
        header = proof.manifest
        offset, previous, found, page_count = 0, finance_evidence.ZERO_DIGEST, [], 0
        while True:
            page = proof.page(offset)
            self.assertEqual(page["previousPageDigest"], previous)
            self.assertEqual(page["pageDigest"], digest({k: v for k, v in page.items() if k != "pageDigest"}))
            self.assertLessEqual(len(canonical(page).encode("utf-8")), finance_evidence.MAX_TOOL_BYTES)
            found.extend(page["rows"])
            page_count += 1
            previous = page["pageDigest"]
            offset = page["pagination"]["nextOffset"]
            if offset is None:
                break
        self.assertEqual([r["id"] for r in found], list(range(1, 105)))
        self.assertEqual(header["pageCount"], page_count)
        self.assertEqual(header["lastPageDigest"], previous)
        self.assertLessEqual(len(canonical(header).encode("utf-8")), finance_evidence.MAX_TOOL_BYTES)
        self.assertGreater(page_count, 2)
        copied = proof.page(); copied["rows"][0]["amount_cents"] = 9
        header["sourceManifest"]["coverage"].clear()
        self.assertEqual(proof.page()["rows"][0]["amount_cents"], records[0]["amount_cents"])
        self.assertTrue(proof.manifest["sourceManifest"]["coverage"])
        with self.assertRaises(AnalysisContractError): proof.page(1)

    def test_empty_exact_scope_remains_gap_and_has_one_empty_page(self):
        source, binding = bound_source([])
        proof = finance_evidence.prepare(source, binding)
        self.assertEqual(proof.manifest["rowCount"], 0)
        self.assertEqual(proof.manifest["pageCount"], 1)
        self.assertEqual(proof.page()["rows"], [])
        self.assertEqual(proof.manifest["sourceManifest"]["coverage"][0]["metrics"]["net_sales"]["status"], "missing_subject")

    def test_full_24_month_selection_keeps_every_missing_month(self):
        months = [f"{year}-{month:02d}" for year in (2025, 2026) for month in range(1, 13)]
        source, binding = bound_source(months=months)
        proof = finance_evidence.prepare(source, binding)
        coverage = proof.manifest["sourceManifest"]["coverage"]
        self.assertEqual(len(coverage), 24)
        self.assertEqual([item["month"] for item in coverage], months)
        self.assertEqual(sum(item["published"] for item in coverage), 1)
        self.assertLessEqual(len(canonical(proof.manifest).encode("utf-8")), finance_evidence.MAX_TOOL_BYTES)

    def test_binding_wrong_revision_batch_or_actor_rejects(self):
        source, binding = bound_source()
        for change in ({"revision": {"revision": 8, "source_digest": "a" * 64}},
                       {"publicationDigest": "0" * 64}, {"persistentEvidenceVerified": True},
                       {"actor": {**binding["actor"], "scope": {}}},
                       {"actor": {**binding["actor"], "role": "operator"}}):
            changed = {**binding, **change}
            with self.assertRaises(AnalysisContractError): finance_evidence.prepare(source, changed)

    def test_tampered_page_or_manifest_does_not_become_evidence(self):
        source, binding = bound_source()
        bad_page = source.page(); bad_page["rows"][0]["amount_cents"] = 9
        forged = finance_source.FinanceSource(source._manifest_json, ((0, canonical(bad_page)),))
        with self.assertRaises(AnalysisContractError): finance_evidence.prepare(forged, binding)
        bad_manifest = source.manifest; bad_manifest["coverage"][0]["metrics"]["net_sales"]["value"] = 9
        forged = finance_source.FinanceSource(canonical(bad_manifest), source._pages)
        with self.assertRaises(AnalysisContractError): finance_evidence.prepare(forged, binding)

    def test_out_of_order_persistent_ids_and_capacity_fail_closed(self):
        source, binding = bound_source([row(2, subject_name="乙"), row(1, subject_name="甲")])
        with self.assertRaises(AnalysisContractError): finance_evidence.prepare(source, binding)
        source, binding = bound_source()
        with patch.object(finance_evidence, "MAX_TOOL_BYTES", 100), self.assertRaises(AnalysisContractError):
            finance_evidence.prepare(source, binding)
        with patch.object(finance_evidence, "MAX_EVIDENCE_BYTES", 100), self.assertRaises(AnalysisContractError):
            finance_evidence.prepare(source, binding)
        with patch.object(finance_evidence, "MAX_PAGES", 0), self.assertRaises(AnalysisContractError):
            finance_evidence.prepare(source, binding)

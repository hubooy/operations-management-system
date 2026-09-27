from contextlib import contextmanager
from copy import deepcopy
import unittest
from unittest.mock import patch

from business_analysis.contracts import AnalysisContractError, digest
from business_analysis.market_dynamics import price_band, rank_entry_exit
from business_analysis.market_report_tables import tables
from business_analysis.test_market_dynamics import BANDS, fixture
from ai_assistant import business_market_export as export
from ai_assistant.policy import AiError


def prepared():
    current, previous = fixture([{"skuId": "A"}, {"skuId": "B"}]), fixture([{"skuId": "B"}, {"skuId": "C"}], window="previous")
    values = {"price_band": price_band(*current, BANDS),
        "rank_entry_exit": rank_entry_exit(*current, *previous)}
    fixed = {"reportId": "synthetic-fixed", "seal": digest("fixed")}
    @contextmanager
    def table(_, source_key, view, __, *, baseline_key=None, bands=None):
        assert source_key == "market"
        assert baseline_key == ("prior" if view == "rank_entry_exit" else None)
        binding = {"schemaVersion": "business-market-dynamics-binding-v1", "reportBinding": fixed,
            "sourceKey": source_key, "baselineKey": baseline_key, "view": view,
            "algorithmVersion": export.owning.market_dynamics.ALGORITHM_VERSION,
            "bandsDigest": digest(BANDS) if bands is not None else None,
            "tableBindingDigest": values[view]["tableDigest"]}
        yield deepcopy(values[view]), binding
    with patch.object(export.owning, "table", side_effect=table), \
            patch.object(export.owning.report_binding, "_revalidate"):
        return export.prepare("synthetic-fixed", "market", "prior", object(), bands=BANDS)


class MarketReportTablesTests(unittest.TestCase):
    def test_complete_three_views_preserve_distinct_grains_and_nulls(self):
        result = prepared()
        manifest = result.manifest
        self.assertEqual([s["view"] for s in manifest["tables"]], list(export.VIEWS))
        self.assertEqual(manifest["rowCount"], 1+2+3)
        self.assertFalse(manifest["authority"]["wholeMarketCoverageVerified"])
        pages = {view: result.ndjson_pages(view) for view in export.VIEWS}
        with tables(manifest, pages) as (meta, rendered):
            self.assertFalse(meta["authorityVerified"])
            summary, members, rank = [list(table.rows) for table in rendered]
            self.assertEqual(sum(row[4] for row in summary), len(members))
            self.assertEqual([row[5] for row in members], ["A", "B"])
            by_sku = {row[3]: row for row in rank}
            self.assertEqual(by_sku["A"][5], "entered_observed_top_sample")
            self.assertEqual(by_sku["A"][15], None)  # No baseline GMV lower bound.
            self.assertEqual(by_sku["C"][5], "left_observed_top_sample")
        with tables(manifest, {view: result.ndjson_pages(view) for view in export.VIEWS}) as (_, again):
            held = again[0].rows
        with self.assertRaises(AnalysisContractError):
            list(held)

    def test_tamper_hash_order_or_page_bytes_reject_without_partial_tables(self):
        original = prepared()
        manifest = original.manifest
        for change in (lambda m,p: m["tables"][0].update(ndjsonSha256="0"*64),
                       lambda m,p: m.update(rowCount=m["rowCount"]+1),
                       lambda m,p: p["rank_entry_exit"].__setitem__(0,
                           b"\n".join(reversed(p["rank_entry_exit"][0].splitlines()))+b"\n"),
                       lambda m,p: p["price_band_summary"].__setitem__(0, p["price_band_summary"][0]+b"{}\n")):
            bad = deepcopy(manifest)
            pages = {view: list(original.ndjson_pages(view)) for view in export.VIEWS}
            change(bad, pages)
            with self.subTest(change=change), self.assertRaises(AnalysisContractError):
                with tables(bad, pages): pass

    def test_caps_and_prepared_copies(self):
        result = prepared()
        first = result.manifest
        first["tables"].clear()
        self.assertEqual(len(result.manifest["tables"]), 3)
        self.assertEqual(sum(len(page) for view in export.VIEWS for page in result.ndjson_pages(view)),
            result.manifest["ndjsonBytes"])
        current = fixture(); previous = fixture(window="previous")
        values = {"price_band": price_band(*current, BANDS), "rank_entry_exit": rank_entry_exit(*current, *previous)}
        @contextmanager
        def table(_, __, view, ___, **kwargs):
            binding = {"reportBinding": {"reportId": "fixed"}, "sourceKey": "market", "baselineKey":
                "prior" if view == "rank_entry_exit" else None, "view": view,
                "algorithmVersion": export.owning.market_dynamics.ALGORITHM_VERSION,
                "bandsDigest": digest(BANDS) if view == "price_band" else None,
                "tableBindingDigest": values[view]["tableDigest"]}
            yield values[view], binding
        with patch.object(export.owning, "table", side_effect=table), \
                patch.object(export.owning.report_binding, "_revalidate"), \
                self.assertRaises(AiError):
            export.prepare("fixed", "market", "prior", object(), bands=BANDS, limits={"maxRows": 1})


if __name__ == "__main__": unittest.main()

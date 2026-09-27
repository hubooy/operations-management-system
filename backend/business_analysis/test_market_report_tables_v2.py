"""Pure composite export and typed-table verification keep all TOP caveats."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from unittest import TestCase
from unittest.mock import patch

from .contracts import AnalysisContractError, canonical, digest
from . import market_dynamics, market_dynamics_v2
from . import market_report_tables_v2 as tables_v2
from .test_market_dynamics import BANDS
from ai_assistant import business_market_composite_export as export
from ai_assistant.test_business_promotion_market_runtime_contract import inputs, source


def prepared(*, missing_baseline_day=False, baseline_top_absent=False):
    current, baseline, selector, _ = inputs()
    if missing_baseline_day:
        baseline = source("previous", ("Z",), observe_last=False)
    elif baseline_top_absent:
        baseline = source("previous", ("C", "A"))
    price = market_dynamics.price_band(*current[:3], BANDS)
    rank = market_dynamics_v2.rank_entry_exit(*current[:3], *baseline[:3],
        selector["currentObservationDate"], selector["baselineObservationDate"])
    fixed = {"reportId": "report-1", "sealedDigest": "a"*64}
    band_binding = {"schemaVersion": "business-market-dynamics-binding-v1",
        "reportBinding": fixed, "sourceKey": current[0]["key"],
        "baselineKey": None, "view": "price_band",
        "algorithmVersion": market_dynamics.ALGORITHM_VERSION,
        "bandsDigest": digest(BANDS), "tableBindingDigest": price["tableDigest"]}
    rank_binding = {"schemaVersion": "business-market-observation-binding-v2",
        "reportBinding": fixed, "currentSourceKey": current[0]["key"],
        "baselineSourceKey": baseline[0]["key"],
        "currentObservationDate": selector["currentObservationDate"],
        "baselineObservationDate": selector["baselineObservationDate"],
        "algorithmVersion": market_dynamics_v2.ALGORITHM_VERSION,
        "sourceProofDigests": {current[0]["key"]: digest(current[2]),
            baseline[0]["key"]: digest(baseline[2])},
        "tableBindingDigest": rank["tableDigest"]}

    @contextmanager
    def price_table(report_id, source_key, view, principal, *, bands):
        assert (report_id, source_key, view, bands) == (
            "report-1", current[0]["key"], "price_band", BANDS)
        yield deepcopy(price), deepcopy(band_binding)

    @contextmanager
    def rank_table(report_id, current_key, baseline_key,
                   current_day, baseline_day, principal):
        assert (report_id, current_key, baseline_key, current_day, baseline_day) == (
            ("report-1", current[0]["key"], baseline[0]["key"],
             selector["currentObservationDate"], selector["baselineObservationDate"]))
        yield deepcopy(rank), deepcopy(rank_binding)

    with patch.object(export.bands_owning, "table", side_effect=price_table), patch.object(
            export.rank_owning, "table", side_effect=rank_table), patch.object(
            export.bands_owning.report_binding, "_revalidate"):
        value = export.prepare("report-1", current[0]["key"], baseline[0]["key"],
            selector["currentObservationDate"], selector["baselineObservationDate"],
            object(), bands=BANDS)
    return value


class MarketReportTablesV2Tests(TestCase):
    def test_three_complete_typed_tables_keep_distinct_roots_and_source_hashes(self):
        material = prepared(); manifest = material.manifest
        self.assertEqual(manifest["schemaVersion"], tables_v2.SCHEMA)
        self.assertEqual([item["view"] for item in manifest["tables"]], list(tables_v2.VIEWS))
        self.assertEqual(manifest["tables"][0]["sourceTableDigest"],
            manifest["tables"][1]["sourceTableDigest"])
        self.assertNotEqual(manifest["tables"][1]["sourceTableDigest"],
            manifest["tables"][2]["sourceTableDigest"])
        self.assertEqual(manifest["tables"][0]["bindingDigest"],
            manifest["tables"][1]["bindingDigest"])
        self.assertNotEqual(manifest["tables"][1]["bindingDigest"],
            manifest["tables"][2]["bindingDigest"])
        self.assertEqual(manifest["tables"][0]["sourceDescriptorDigest"],
            manifest["tables"][1]["sourceDescriptorDigest"])
        self.assertNotEqual(manifest["tables"][1]["sourceDescriptorDigest"],
            manifest["tables"][2]["sourceDescriptorDigest"])
        self.assertFalse(manifest["authority"]["priceSummaryAndMembersAdditive"])
        self.assertFalse(manifest["authority"]["marketAndOwnSalesAdditive"])
        with tables_v2.tables(manifest, {view: material.ndjson_pages(view)
                for view in tables_v2.VIEWS}) as (summary, tables):
            self.assertFalse(summary["authorityVerified"])
            rows = [list(table.rows) for table in tables]
            self.assertEqual([len(part) for part in rows],
                [item["rowCount"] for item in manifest["tables"]])
            rank = rows[2][0]
            self.assertEqual(rank[3], "A")
            self.assertEqual(rank[5], "both_observed")
            self.assertEqual(len(rank[17]), 64)
            self.assertEqual(len(rank[18]), 64)
            self.assertIn("sourceRowHash", rank[-1])

    def test_missing_observation_day_keeps_null_baseline_and_not_a_top_exit(self):
        material = prepared(missing_baseline_day=True)
        manifest = material.manifest
        self.assertFalse(manifest["rankObservationCoverage"]["bothDatesPresent"])
        with tables_v2.tables(manifest, {view: material.ndjson_pages(view)
                for view in tables_v2.VIEWS}) as (_, tables):
            rank = list(tables[2].rows)[0]
            self.assertEqual(rank[5], "insufficient_date_coverage")
            self.assertEqual(rank[7], "date_not_covered")
            self.assertIsNone(rank[15])
            self.assertIsNone(rank[16])
            self.assertIsNone(rank[18])
            self.assertIn('"metrics":null', rank[-1])

    def test_recomputed_material_digests_cannot_smuggle_invalid_identity_or_date_state(self):
        def altered(material, view, change):
            manifest = material.manifest
            pages = {name: tuple(material.ndjson_pages(name)) for name in tables_v2.VIEWS}
            rows = [json.loads(line) for line in pages[view][0].splitlines()]
            change(rows[0])
            spec = next(item for item in manifest["tables"] if item["view"] == view)
            root = spec["sourceTableDigest"]
            body = {key: value for key, value in rows[0].items()
                if key not in {"rowIndex", "id"}}
            rows[0]["id"] = digest([root, view, rows[0]["rowIndex"], body])
            raw = b"".join((canonical(row)+"\n").encode("utf-8") for row in rows)
            pages[view] = (raw,)
            spec["ndjsonBytes"] = len(raw)
            spec["ndjsonSha256"] = hashlib.sha256(raw).hexdigest()
            manifest["ndjsonBytes"] = sum(item["ndjsonBytes"] for item in manifest["tables"])
            manifest["manifestDigest"] = digest({key: value for key, value
                in manifest.items() if key != "manifestDigest"})
            with self.assertRaises(AnalysisContractError):
                with tables_v2.tables(manifest, pages):
                    pass

        altered(prepared(), "rank_entry_exit", lambda row: row.update(skuId=42))
        altered(prepared(), "rank_entry_exit", lambda row: row.update(spuId=True, skuId=None))
        altered(prepared(), "price_band_members", lambda row: row.update(skuId=True))
        altered(prepared(), "price_band_members", lambda row: row.update(spuId=123, skuId=None))
        altered(prepared(missing_baseline_day=True), "rank_entry_exit",
            lambda row: row["baseline"].update(status="not_observed_in_top_sample"))
        altered(prepared(baseline_top_absent=True), "rank_entry_exit",
            lambda row: row["baseline"].update(status="date_not_covered"))

    def test_tampered_manifest_or_ndjson_or_nonadditivity_reject(self):
        material = prepared(); original = material.manifest
        for mutate in (lambda value: value["authority"].update(priceSummaryAndMembersAdditive=True),
                       lambda value: value["tables"][2]["binding"].update(currentSourceKey="other"),
                       lambda value: value["tables"][2].update(sourceDescriptorDigest="0"*64),
                       lambda value: value["tables"][2].update(sourceTableDigest="0"*64),
                       lambda value: value["rankObservationCoverage"].update(bothDatesPresent=False)):
            bad = deepcopy(original); mutate(bad)
            bad["manifestDigest"] = digest({key: child for key, child in bad.items()
                if key != "manifestDigest"})
            with self.assertRaises(AnalysisContractError):
                with tables_v2.tables(bad, {view: material.ndjson_pages(view)
                        for view in tables_v2.VIEWS}):
                    pass
        pages = {view: tuple(material.ndjson_pages(view)) for view in tables_v2.VIEWS}
        pages["rank_entry_exit"] = (pages["rank_entry_exit"][0].replace(b'A', b'X', 1),)
        with self.assertRaises(AnalysisContractError):
            with tables_v2.tables(original, pages):
                pass

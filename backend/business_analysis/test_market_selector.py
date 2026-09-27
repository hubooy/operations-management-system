from copy import deepcopy
import unittest

from .contracts import AnalysisContractError, digest
from .market_selector import freeze, validate


CONTEXT = {"reportId": "formal-1", "runId": "evidence-1", "screeningId": "screen-1",
    "sealedDigest": "a"*64}
BANDS = [{"key": "low", "lowerCents": 0, "upperExclusiveCents": 10000},
    {"key": "mid", "lowerCents": 10000, "upperExclusiveCents": 50000}]


def source(key, window="current", *, date="2026-09-01", category="饮水机", dimension="SKU"):
    return {"key": key, "domain": "market", "query": {"platform": "京东", "category": category,
        "scope": "POP", "rankingDimension": dimension, "priceBandFilter": "全部",
        "startDate": date, "endDate": date, "window": window}}


class MarketSelectorTests(unittest.TestCase):
    def test_single_day_current_and_explicit_previous_bind_complete_directory(self):
        sources = [source("now"), source("prior", "previous"), source("other", "current", category="净水机")]
        before = deepcopy((sources, CONTEXT, BANDS))
        choice = {"sourceKey": "now", "baselineKey": "prior", "bands": BANDS}
        value = freeze(sources, CONTEXT, choice)
        self.assertEqual(value["marketSelector"]["views"], ["price_band", "rank_entry_exit"])
        self.assertEqual(value["source"]["query"]["rankingDimension"], "SKU")
        self.assertEqual(value["baseline"]["query"]["window"], "previous")
        self.assertEqual(value["selectorDigest"], digest({k:v for k,v in value.items() if k != "selectorDigest"}))
        self.assertFalse(value["authorityVerified"])
        self.assertFalse(value["ownProductIdentityVerified"])
        self.assertFalse(value["wholeMarketCoverageVerified"])
        self.assertEqual(validate(sources, CONTEXT, choice, value), value)
        self.assertEqual((sources, CONTEXT, BANDS), before)
        self.assertNotEqual(value["catalogDigest"], freeze(sources[:2], CONTEXT,
            {"sourceKey": "now", "baselineKey": "prior", "bands": BANDS})["catalogDigest"])

    def test_price_band_only_can_use_multi_day_current_without_claiming_entry_exit(self):
        current = source("now")
        current["query"].update(startDate="2026-09-01", endDate="2026-09-20")
        value = freeze([current], CONTEXT, {"sourceKey": "now", "bands": BANDS})
        self.assertEqual(value["marketSelector"]["views"], ["price_band"])
        self.assertIsNone(value["baseline"])
        self.assertEqual(value["periods"]["current"]["days"], 20)
        self.assertIn("缺席", "".join(value["limitations"]))

    def test_baseline_requires_exact_single_day_same_category_grain_and_filter(self):
        current = source("now")
        baseline = source("prior", "yearAgo")
        self.assertEqual(freeze([current, baseline], CONTEXT,
            {"sourceKey": "now", "baselineKey": "prior", "bands": BANDS})["baseline"]["query"]["window"], "yearAgo")
        for changed in ({"category": "其他"}, {"rankingDimension": "SPU"},
            {"priceBandFilter": "0-500"}, {"scope": "SELF"}):
            wrong = source("prior", "previous")
            wrong["query"].update(changed)
            with self.subTest(changed=changed), self.assertRaises(AnalysisContractError):
                freeze([current, wrong], CONTEXT,
                    {"sourceKey": "now", "baselineKey": "prior", "bands": BANDS})
        multi = source("now")
        multi["query"]["endDate"] = "2026-09-02"
        base = source("prior", "previous")
        base["query"]["endDate"] = "2026-09-02"
        with self.assertRaises(AnalysisContractError):
            freeze([multi, base], CONTEXT, {"sourceKey": "now", "baselineKey": "prior", "bands": BANDS})

    def test_no_inferred_baseline_cross_platform_or_duplicate_choice(self):
        sources = [source("now"), source("prior", "previous")]
        for choice in ({"sourceKey": "missing", "bands": BANDS},
                       {"sourceKey": "prior", "bands": BANDS},
                       {"sourceKey": "now", "baselineKey": "now", "bands": BANDS},
                       {"sourceKey": "now", "baselineKey": "missing", "bands": BANDS},
                       {"sourceKey": "now", "bands": BANDS, "shop": "自家店"}):
            with self.subTest(choice=choice), self.assertRaises(AnalysisContractError):
                freeze(sources, CONTEXT, choice)
        other = source("other", "current")
        other["query"]["platform"] = "天猫"
        with self.assertRaises(AnalysisContractError):
            freeze([other], CONTEXT, {"sourceKey": "other", "bands": BANDS})

    def test_bands_finite_ordered_unambiguous_and_not_relabelled(self):
        source_list = [source("now")]
        for bad in ([{"key": "x", "lowerCents": 0, "upperExclusiveCents": None}],
            [{"key": "x", "lowerCents": True, "upperExclusiveCents": 1}],
            [BANDS[1], BANDS[0]],
            [BANDS[0], {"key": "bad", "lowerCents": 9000, "upperExclusiveCents": 11000}],
            [BANDS[0], {**BANDS[1], "key": "low"}],
            [{**BANDS[0], "key": "unallocated_missing"}],
            [{"key": "huge", "lowerCents": 0, "upperExclusiveCents": 2**53}]):
            with self.subTest(bad=bad), self.assertRaises(AnalysisContractError):
                freeze(source_list, CONTEXT, {"sourceKey": "now", "bands": bad})
        valid = freeze(source_list, CONTEXT, {"sourceKey": "now", "bands": BANDS})
        self.assertEqual(valid["marketSelector"]["bands"], BANDS)

    def test_rehashed_claim_does_not_replace_independent_context_or_directory(self):
        sources = [source("now")]
        choice = {"sourceKey": "now", "bands": BANDS}
        frozen = freeze(sources, CONTEXT, choice)
        for index,change in enumerate((lambda v: v["marketSelector"]["views"].append("other"),
                       lambda v: v.update(authorityVerified=True),
                       lambda v: v["source"]["query"].update(category="改写"),
                       lambda v: v["context"].update(reportId="another"),
                       lambda v: v["marketSelector"]["bands"][0].update(upperExclusiveCents=9999))):
            forged = deepcopy(frozen); change(forged)
            forged["selectorDigest"] = digest({k:v for k,v in forged.items() if k != "selectorDigest"})
            with self.subTest(index=index), self.assertRaises(AnalysisContractError):
                validate(sources, CONTEXT, choice, forged)


if __name__ == "__main__": unittest.main()

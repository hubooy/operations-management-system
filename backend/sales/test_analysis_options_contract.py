from copy import deepcopy
import unittest
from . import analysis_options_contract as c


def group(platform="京东", shop="合成店", channel="京东渠道", **changes):
    return {"platform": platform, "shop_name": shop, "channel": channel,
        "platform_key": platform, "shop_key": shop, "channel_key": channel,
        "firstDate": "2026-09-01", "lastDate": "2026-09-18", "rowCount": 2, **changes}


def page(groups, **changes):
    return c.make_page(groups, **{"query": {}, "revision": "12:7", "has_more": False, "next_cursor": None, **changes})


class SalesOptionsContractTests(unittest.TestCase):
    def test_exact_three_part_identity_multiple_platforms_and_channels(self):
        rows = [group("京东", "同名店", "渠道A"), group("京东", "同名店", "渠道B"), group("天猫", "同名店", "渠道A")]
        value = page(rows)
        self.assertEqual(len({item["optionKey"] for item in value["items"]}), 3)
        self.assertEqual([item["identity"]["channel"] for item in value["items"]], ["渠道A", "渠道B", "渠道A"])
        self.assertFalse(value["authorityVerified"])
        self.assertNotIn("customer", str(value))

    def test_date_envelope_with_gaps_never_claims_coverage(self):
        value = page([group()])
        self.assertEqual(value["items"][0]["dateMetadata"], {"kind":"current_fact_business_date_envelope", "firstDate":"2026-09-01", "lastDate":"2026-09-18", "snapshotDate":None, "coverageVerified":False})
        self.assertFalse(page([])["pagination"]["hasMore"])

    def test_empty_fallback_and_trimmed_raw_identity_rejected(self):
        for value in [group(shop="", shop_key="京东渠道"), group(platform="", platform_key="未分类"),
                      group(channel="", channel_key="未分类"), group(shop=" 合成店", shop_key="合成店"),
                      group(shop="合成店旗舰店", shop_key="合成店")]:
            with self.subTest(value=value), self.assertRaises(c.OptionsContractError): page([value])
        # A literal nonempty source value is not confused with an injected fallback.
        self.assertEqual(page([group(shop="未分类")])["items"][0]["identity"]["shop"], "未分类")

    def test_successful_import_scope_and_erp_master_cannot_invent_identity(self):
        for value in [{"source":"sales_ledger","startDate":"2026-09-01","endDate":"2026-09-18","channels":["京东渠道"]},
                      {"product_code":"ERP-1","product_name":"合成产品"}, group(extra="ignored")]:
            with self.assertRaises(c.OptionsContractError): page([value])

    def test_strict_exact_filters_without_casefold_or_alias(self):
        self.assertEqual(c.normalize_query({"platform":"任意原平台", "channel":"精确渠道"})["platform"], "任意原平台")
        for query in [{"shop":"合成店"}, {"q":"店"}, {"limit":20}, {"platform":" 京东"}, {"platform":"京东", "channel":None}]:
            with self.assertRaises(c.OptionsContractError): c.normalize_query(query)
        for query in [{"platform":"天猫"}, {"channel":"京东渠道别名"}]:
            with self.assertRaises(c.OptionsContractError): page([group()], query=query)

    def test_dates_counts_types_and_unicode_validation(self):
        for value in [group(firstDate="2026-02-30"), group(firstDate="2026-10-01"),
                      group(firstDate=None), group(lastDate="2026-9-18"), group(rowCount=True),
                      group(rowCount=1.0), group(rowCount=0), group(rowCount=c.MAX_SAFE_INTEGER+1),
                      group(shop="店\x7f"), group(shop="店\ud800"), group(shop="中"*201)]:
            with self.subTest(value=repr(value)), self.assertRaises(c.OptionsContractError): page([value])
        self.assertEqual(page([group(shop="ßİ🚀")])["items"][0]["identity"]["shop"], "ßİ🚀")

    def test_canonical_page_digest_detaches_input(self):
        rows = [group()]; value = page(rows); expected = deepcopy(value)
        rows[0]["shop_name"] = "changed"
        self.assertEqual(value, expected)
        actual = deepcopy(value); checksum=actual.pop("pageDigest")
        self.assertEqual(checksum, c.digest(actual))

    def test_twenty_rows_and_signed_cursor_boundary_is_owning_responsibility(self):
        rows = [group(shop=f"店{i:03}") for i in range(20)]
        self.assertEqual(page(rows, has_more=True, next_cursor="opaque")['pagination']['returned'],20)
        for kwargs in [{"has_more":1}, {"next_cursor":"opaque"}, {"has_more":True,"next_cursor":None}]:
            with self.assertRaises(c.OptionsContractError): page(rows, **kwargs)
        with self.assertRaises(c.OptionsContractError): page(rows[:19], has_more=True, next_cursor="opaque")
        with self.assertRaises(c.OptionsContractError): page(rows+[group(shop="店999")])

    def test_full_identity_order_and_cross_page_boundary(self):
        first, second = group(shop="店A"), group(shop="店B")
        previous = c.normalize_group(first)["identity"]
        self.assertEqual(page([second], previous_identity=previous)["pagination"]["returned"],1)
        for rows, prev in [([first,first],None),([second,first],None),([first],previous)]:
            with self.assertRaises(c.OptionsContractError): page(rows,previous_identity=prev)

    def test_full_page_utf8_limit_rejects_without_truncation(self):
        rows=[group(platform="京"*200,shop=f"{i:03}"+"店"*197,channel="渠"*200) for i in range(20)]
        with self.assertRaisesRegex(c.OptionsContractError,"38000"): page(rows)
        # Short valid pages preserve exact raw identity and stay within bytes.
        value=page([rows[0]])
        self.assertLessEqual(len(c.canonical(value).encode()),38000)
        self.assertEqual(value['items'][0]['identity']['channel'],"渠"*200)

    def test_revision_is_exact_dual_safe_integer_not_market_digest(self):
        for revision in ["1:abcdef123456","01:2","-1:2","1:2:3", "1:"+str(c.MAX_SAFE_INTEGER+1), None,True]:
            with self.assertRaises(c.OptionsContractError): page([],revision=revision)
        self.assertEqual(page([],revision="0:0")["revision"],"0:0")


if __name__ == '__main__': unittest.main()

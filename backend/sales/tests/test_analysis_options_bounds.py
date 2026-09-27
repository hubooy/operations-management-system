"""Pure fixed capacity checks; no database access or production claims."""
from django.test import SimpleTestCase
from sales import analysis_options_projection as projection
from sales import analysis_options_contract as contract


def group(index, *, wide=False):
    platform = "界" * 200 if wide else "京东"
    shop = ("店" * 194 + f"{index:06d}") if wide else f"店{index:06d}"
    channel = "渠" * 200 if wide else "渠道"
    return {"platform": platform, "shop_name": shop, "channel": channel,
        "platform_key": platform, "shop_key": shop, "channel_key": channel,
        "firstDate": "2026-09-01", "lastDate": "2026-09-17", "rowCount": 1}


class SalesOptionsBoundsTests(SimpleTestCase):
    def test_exact_ten_thousand_groups_fit_and_next_group_is_not_truncated(self):
        values = [group(i) for i in range(10_000)]
        accepted = projection._entries(values)
        self.assertEqual(len(accepted), 10_000)
        self.assertLess(len(contract.canonical(accepted).encode()), 16 * 1024 * 1024)
        with self.assertRaises(projection.OptionsError):
            projection._entries([*values, group(10_000)])
        self.assertEqual(projection.MAX_IDENTITIES, 10_000)

    def test_actual_utf8_directory_budget_rejects_below_identity_limit(self):
        values = [group(i, wide=True) for i in range(5_000)]
        self.assertGreater(len(contract.canonical(values).encode()), 16 * 1024 * 1024)
        with self.assertRaises(projection.OptionsError):
            projection._entries(values)
        self.assertEqual(projection.MAX_DIRECTORY_BYTES, 16 * 1024 * 1024)

    def test_wide_twenty_identity_page_is_rejected_without_shrinking(self):
        values = [group(i, wide=True) for i in range(20)]
        with self.assertRaises(contract.OptionsContractError):
            contract.make_page(values, query={}, revision="1:1", has_more=False, next_cursor=None)
        self.assertEqual(len(values),20)

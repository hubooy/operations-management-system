import hashlib
import json
from unittest.mock import patch

from django.test import TestCase

from finance.models import FinanceTarget, FinanceWriteAuthority
from finance.target_service import import_annual_targets, upsert_target
from sales.tests.factories import TEST_SECRET, signed_headers

from .factories import body_bytes, prepared_payload
from finance.import_service import import_finance_payload


def import_payload(*rows):
    return {
        "schemaVersion": "finance-annual-target-import-v1",
        "year": "2026",
        "fileName": "店铺年度目标.xlsx",
        "fileSizeBytes": 12_630,
        "fileSha256": hashlib.sha256(b"annual-targets").hexdigest(),
        "sheetName": "Sheet1",
        "headerRowNumber": 4,
        "sourceRowCount": len(rows),
        "skippedRowCount": 0,
        "rows": list(rows),
    }


def target_row(row_number, store_label, **overrides):
    return {
        "rowNumber": row_number,
        "storeLabel": store_label,
        "manager": "负责人",
        "salesTargetCents": 10_000_000,
        "profitTargetCents": 1_000_000,
        "grossMarginBps": 4_500,
        "promotionFeeRatioBps": 500,
        **overrides,
    }


@patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
class AnnualTargetImportTests(TestCase):
    def setUp(self):
        FinanceWriteAuthority.objects.filter(id=1).update(status="postgres")
        import_finance_payload(prepared_payload("2026-01"), "fixture@example.invalid")

    def test_import_atomically_creates_updates_and_preserves_unlisted_targets(self):
        existing = upsert_target({
            "periodType": "year", "periodKey": "2026", "platform": "京东", "shopName": "同名店",
            "manager": "旧负责人", "salesTargetCents": 1, "profitTargetCents": 2,
            "smallMarginBps": 3_300, "inventoryCleanupTargetCents": 99,
        })[0]
        unlisted = upsert_target({
            "periodType": "year", "periodKey": "2026", "platform": "其他", "shopName": "保留店",
            "salesTargetCents": 123,
        })[0]
        payload = import_payload(
            target_row(5, "京东-同名店", manager="新负责人"),
            target_row(6, "天猫-同名店", salesTargetCents=20_000_000, grossMarginBps=4_600),
        )
        result = import_annual_targets(payload)
        self.assertEqual((result["createdCount"], result["updatedCount"], result["importedCount"]), (1, 1, 2))
        jd = FinanceTarget.objects.get(id=existing["id"])
        self.assertEqual(jd.version, 2)
        self.assertEqual(jd.manager, "新负责人")
        self.assertEqual(jd.sales_target_cents, 10_000_000)
        self.assertEqual(jd.gross_margin_bps, 4_500)
        self.assertEqual(jd.promotion_fee_ratio_bps, 500)
        self.assertEqual(jd.small_margin_bps, 3_300)
        self.assertEqual(jd.inventory_cleanup_target_cents, 99)
        self.assertTrue(FinanceTarget.objects.filter(id=unlisted["id"], sales_target_cents=123).exists())

    def test_ambiguous_or_unknown_shop_rejects_without_partial_write(self):
        for label in ("同名店", "未知平台-未知店"):
            with self.subTest(label=label), self.assertRaisesMessage(Exception, "店铺"):
                import_annual_targets(import_payload(target_row(5, "京东-同名店"), target_row(6, label)))
            self.assertFalse(FinanceTarget.objects.filter(period_type="year", period_key="2026").exists())

    def test_unique_trailing_note_alias_resolves_to_canonical_shop(self):
        canonical = upsert_target({
            "periodType": "year", "periodKey": "2026", "platform": "志高京东组",
            "shopName": "京东-志高商用设备旗舰店（亿用）", "salesTargetCents": 1,
        })[0]
        result = import_annual_targets(import_payload(
            target_row(15, "京东-志高商用设备旗舰店", salesTargetCents=16_000_000),
        ))
        self.assertEqual((result["createdCount"], result["updatedCount"]), (0, 1))
        updated = FinanceTarget.objects.get(id=canonical["id"])
        self.assertEqual(updated.shop_name, "京东-志高商用设备旗舰店（亿用）")
        self.assertEqual(updated.sales_target_cents, 16_000_000)
        self.assertFalse(FinanceTarget.objects.filter(shop_name="京东-志高商用设备旗舰店").exists())

    def test_trailing_note_alias_rejects_multiple_candidates(self):
        for platform, shop_name in (
            ("志高京东组", "京东-志高商用设备旗舰店（亿用）"),
            ("另一业务组", "京东-志高商用设备旗舰店（另一店）"),
        ):
            upsert_target({
                "periodType": "year", "periodKey": "2026", "platform": platform,
                "shopName": shop_name, "salesTargetCents": 1,
            })
        with self.assertRaisesMessage(Exception, "匹配到多个候选"):
            import_annual_targets(import_payload(
                target_row(15, "京东-志高商用设备旗舰店", salesTargetCents=16_000_000),
            ))
        self.assertEqual(
            list(FinanceTarget.objects.order_by("shop_name").values_list("sales_target_cents", flat=True)),
            [1, 1],
        )

    def test_writer_endpoint_is_replay_fenced(self):
        payload = import_payload(target_row(5, "京东-同名店"))
        body = body_bytes(payload)
        url = "/api/finance/targets/import"
        headers = signed_headers(url, method="POST", body=body, request_id="annual-target-import")
        first = self.client.post(url, data=body, content_type="application/json; charset=utf-8", headers=headers)
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(first.json()["importedCount"], 1)
        replay = self.client.post(url, data=body, content_type="application/json; charset=utf-8", headers=headers)
        self.assertEqual(replay.status_code, 201, replay.content)
        self.assertEqual(replay["X-Teruisi-Write-Replay"], "1")
        changed = json.loads(body)
        changed["rows"][0]["salesTargetCents"] = 99
        changed_body = body_bytes(changed)
        collision = self.client.post(
            url,
            data=changed_body,
            content_type="application/json; charset=utf-8",
            headers=signed_headers(url, method="POST", body=changed_body, request_id="annual-target-import"),
        )
        self.assertEqual(collision.status_code, 409)

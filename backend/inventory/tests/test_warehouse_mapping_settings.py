from __future__ import annotations

import uuid

from django.test import TestCase
from django.utils import timezone

from inventory.models import InventoryOperatingSettings, InventoryWriteAuthority
from inventory.warehouse_mapping import classify_warehouse
from inventory.warehouse_mapping_service import (
    effective_mapping,
    mapping_payload,
    update_mapping,
)


class WarehouseMappingSettingsTests(TestCase):
    def setUp(self):
        InventoryWriteAuthority.objects.filter(id=1).update(
            status="postgres",
            authority_epoch=uuid.uuid4(),
            cutover_id="warehouse-mapping",
            migration_verify_run_id="inventory-apply-" + "1" * 32,
            activated_at=timezone.now(),
        )

    def test_default_payload_uses_the_verified_release_mapping(self):
        payload = mapping_payload()
        self.assertEqual(len(payload["rows"]), 284)
        self.assertEqual(sum(bool(row["includeInInventory"]) for row in payload["rows"]), 66)
        self.assertEqual(payload["pendingConfirmationCount"], 0)
        self.assertFalse(any(bool(row["pendingConfirmation"]) for row in payload["rows"]))
        self.assertRegex(str(payload["mappingRevision"]), r"^[a-f0-9]{64}$")

    def test_single_edit_persists_and_becomes_the_effective_classifier(self):
        before = mapping_payload()
        after = update_mapping(
            {
                "expectedMappingRevision": before["mappingRevision"],
                "mappings": [{
                    "warehouse": "一个小太阳仓",
                    "category": "afterSales",
                    "includeInInventory": True,
                }],
            },
            "admin@example.test",
        )
        row = next(item for item in after["rows"] if item["warehouse"] == "一个小太阳仓")
        self.assertEqual(row["label"], "售后仓")
        self.assertTrue(row["includeInInventory"])
        self.assertFalse(row["pendingConfirmation"])
        stored = InventoryOperatingSettings.objects.get(id=1)
        classification = classify_warehouse("一个小太阳仓", mapping=effective_mapping(stored))
        self.assertEqual((classification.category, classification.label), ("afterSales", "售后仓"))
        self.assertTrue(classification.include_in_inventory)

    def test_batch_import_merges_without_deleting_unlisted_warehouses(self):
        before = mapping_payload()
        after = update_mapping(
            {
                "expectedMappingRevision": before["mappingRevision"],
                "mappings": [{
                    "warehouse": "新增测试仓",
                    "category": "sample",
                    "includeInInventory": False,
                }],
            },
            "admin@example.test",
        )
        self.assertEqual(len(after["rows"]), 285)
        self.assertIn("ZA菜鸟华中武汉黄陂标准03仓", {row["warehouse"] for row in after["rows"]})

    def test_stale_mapping_revision_is_rejected(self):
        before = mapping_payload()
        payload = {
            "expectedMappingRevision": before["mappingRevision"],
            "mappings": [{
                "warehouse": "三合仓",
                "category": "dropship",
                "includeInInventory": True,
            }],
        }
        update_mapping(payload, "admin@example.test")
        with self.assertRaisesRegex(Exception, "刷新后重试"):
            update_mapping(payload, "admin@example.test")

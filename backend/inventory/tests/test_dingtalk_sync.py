from __future__ import annotations

from datetime import date
import json
import uuid
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from inventory.dingtalk_sync import DingTalkReplenishmentGateway, DwsCli, load_target, sync_replenishment_plan
from inventory.errors import InventoryApiError
from inventory.models import InventoryWriteAuthority, ReplenishmentPlanItem


class FakeDws:
    def __init__(self) -> None:
        self.target = load_target()
        self.created_cells: dict[str, object] | None = None
        self.commands: list[list[str]] = []

    def __call__(self, args: list[str]) -> dict[str, object]:
        self.commands.append(args)
        path = tuple(args[:3])
        if path == ("aitable", "table", "get"):
            return {"data": {"tables": [{
                "tableId": self.target.table_id,
                "tableName": self.target.table_name,
                "fields": [
                    {"fieldId": field["id"], "fieldName": field["name"], "type": field["type"]}
                    for field in self.target.fields.values()
                ],
            }]}}
        if path == ("aitable", "field", "get"):
            names = {
                self.target.fields["brand"]["id"]: ["志高", "特睿思"],
                self.target.fields["warehouse"]["id"]: ["广东仓", "京东自营实物仓"],
                self.target.fields["requiresInspection"]["id"]: ["是", "否"],
            }
            field_id = args[args.index("--field-ids") + 1]
            if field_id not in names or "," in field_id:
                raise AssertionError(f"field get must use one exact field id: {field_id}")
            return {"data": {"fields": [{
                "fieldId": field_id,
                "config": {"options": [{"id": f"opt-{index}", "name": name} for index, name in enumerate(options)]},
            } for field_id, options in [(field_id, names[field_id])]]}}
        if path == ("contact", "user", "search"):
            name = args[args.index("--query") + 1]
            return {"result": [{"name": name, "userId": f"user-{name}"}]}
        if path == ("contact", "dept", "search"):
            name = args[args.index("--query") + 1]
            return {"deptList": [{"deptId": 903638406, "deptName": f"公司-运营部-<red>{name}</red>"}]}
        if path == ("aitable", "record", "create"):
            records = json.loads(args[args.index("--records") + 1])
            self.created_cells = records[0]["cells"]
            return {"success": True, "data": {"recordIds": ["record-1"]}}
        if path == ("aitable", "record", "update"):
            records = json.loads(args[args.index("--records") + 1])
            self.created_cells = records[0]["cells"]
            return {"success": True, "data": {"recordIds": ["record-1"]}}
        if path == ("aitable", "record", "query"):
            if self.created_cells is None:
                return {"data": {"records": []}}
            cells: dict[str, object] = {}
            by_id = {field["id"]: field for field in self.target.fields.values()}
            for field_id, value in self.created_cells.items():
                field_type = by_id[field_id]["type"]
                if field_type == "number":
                    cells[field_id] = str(value)
                elif field_type == "date":
                    cells[field_id] = f"{value}T00:00:00+08:00"
                elif field_type == "singleSelect":
                    cells[field_id] = {"id": f"opt-{value}", "name": value}
                elif field_type == "department":
                    cells[field_id] = [{"departmentId": row["deptId"], "name": "志高项目组"} for row in value]
                else:
                    cells[field_id] = value
            return {"data": {"records": [{"recordId": "record-1", "cells": cells}]}}
        raise AssertionError(f"unexpected DWS command: {args}")


def make_plan(*, status: str = "confirmed") -> ReplenishmentPlanItem:
    return ReplenishmentPlanItem.objects.create(
        id=str(uuid.uuid4()),
        source_batch_id="batch-1",
        product_code="P1",
        product_name="测试货品",
        brand="特睿思",
        category="净水设备",
        supplier="供应商甲",
        warehouse="广东仓",
        buyer="梁家明",
        operator_name="胡博",
        department="志高项目组",
        plan_type="日常备货",
        order_date=date(2026, 9, 3),
        expected_arrival_date=date(2026, 9, 10),
        requires_inspection=True,
        current_stock_quantity=3,
        sales_30d_quantity=1,
        suggested_quantity=3,
        planned_quantity=3,
        coverage_days_tenths=1800,
        notes="测试备注",
        reason="人工创建",
        status=status,
    )


class DingTalkReplenishmentGatewayTests(TestCase):
    def test_incomplete_marker_query_never_creates_or_updates_a_record(self) -> None:
        plan = make_plan()
        invalid_queries = [
            {}, {"data": {}}, {"data": {"records": None}},
            {"data": {"records": [{}]}},
            {"data": {"records": [{"recordId": 12}]}},
            {"data": {"records": [], "hasMore": True}},
            {"data": {"records": [{"recordId": str(i)} for i in range(10)]}},
        ]
        for payload in invalid_queries:
            with self.subTest(payload=payload):
                fake = FakeDws()

                def runner(args):
                    if args[:3] == ["aitable", "record", "query"]:
                        return payload
                    return fake(args)

                gateway = DingTalkReplenishmentGateway(fake.target, DwsCli(fake.target, runner))
                with self.assertRaisesMessage(InventoryApiError, "避免重复创建"):
                    gateway.sync(plan)
                self.assertIsNone(fake.created_cells)
                self.assertFalse(any(args[:3] in (["aitable", "record", "create"], ["aitable", "record", "update"]) for args in fake.commands))

    def test_maps_and_rechecks_every_written_field(self) -> None:
        plan = make_plan()
        fake = FakeDws()
        gateway = DingTalkReplenishmentGateway(fake.target, DwsCli(fake.target, fake))

        record_id, outcome = gateway.sync(plan)

        self.assertEqual(record_id, "record-1")
        self.assertEqual(outcome, "created")
        assert fake.created_cells is not None
        fields = fake.target.fields
        self.assertEqual(fake.created_cells[fields["plannedQuantity"]["id"]], 3)
        self.assertEqual(fake.created_cells[fields["warehouse"]["id"]], "广东仓")
        self.assertEqual(fake.created_cells[fields["buyer"]["id"]][0]["userId"], "user-梁家明")
        self.assertIn("[运营管理系统备货计划ID:", fake.created_cells[fields["notes"]["id"]])
        self.assertNotIn("[TERUISI备货计划ID:", fake.created_cells[fields["notes"]["id"]])
        field_gets = [command for command in fake.commands if command[:3] == ["aitable", "field", "get"]]
        self.assertEqual(len(field_gets), 3)
        self.assertTrue(all("," not in command[command.index("--field-ids") + 1] for command in field_gets))
        marker_queries = [command for command in fake.commands if command[:3] == ["aitable", "record", "query"]]
        self.assertEqual(len(marker_queries), 4)
        filters = [command[command.index("--filters") + 1] for command in marker_queries]
        self.assertTrue(any("运营管理系统备货计划ID" in value for value in filters))
        self.assertTrue(any("TERUISI备货计划ID" in value for value in filters))


class ReplenishmentSyncStateTests(TestCase):
    def setUp(self) -> None:
        InventoryWriteAuthority.objects.filter(id=1).update(
            status="postgres",
            authority_epoch=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            cutover_id="inventory-test-cutover",
            migration_verify_run_id="inventory-apply-" + "1" * 32,
            activated_at=timezone.now(),
        )

    def test_confirmed_plan_is_synced_once_and_receipt_is_reused(self) -> None:
        plan = make_plan()

        class Gateway:
            target = load_target()

            def __init__(self) -> None:
                self.calls = 0

            def sync(self, _plan: ReplenishmentPlanItem) -> tuple[str, str]:
                self.calls += 1
                return "record-1", "created"

        gateway = Gateway()
        result = sync_replenishment_plan(plan.id, "operator@example.test", gateway=gateway)
        repeated = sync_replenishment_plan(plan.id, "operator@example.test", gateway=gateway)
        plan.refresh_from_db()

        self.assertEqual(result["outcome"], "created")
        self.assertEqual(repeated["outcome"], "already_synced")
        self.assertEqual(gateway.calls, 1)
        self.assertEqual(plan.dingtalk_sync_status, "synced")
        self.assertEqual(plan.dingtalk_record_id, "record-1")
        self.assertEqual(plan.dingtalk_synced_by, "operator@example.test")
        self.assertIsNotNone(plan.dingtalk_synced_at)

    def test_legacy_payload_digest_is_resynced_to_migrate_the_marker(self) -> None:
        plan = make_plan()
        plan.dingtalk_sync_status = "synced"
        plan.dingtalk_record_id = "record-legacy"
        plan.dingtalk_payload_sha256 = "0" * 64
        plan.save(update_fields=["dingtalk_sync_status", "dingtalk_record_id", "dingtalk_payload_sha256"])

        class Gateway:
            target = load_target()

            def __init__(self) -> None:
                self.calls = 0

            def sync(self, _plan: ReplenishmentPlanItem) -> tuple[str, str]:
                self.calls += 1
                return "record-legacy", "updated"

        gateway = Gateway()
        result = sync_replenishment_plan(plan.id, "operator@example.test", gateway=gateway)
        plan.refresh_from_db()

        self.assertEqual(result["outcome"], "updated")
        self.assertEqual(gateway.calls, 1)
        self.assertNotEqual(plan.dingtalk_payload_sha256, "0" * 64)

    def test_draft_plan_is_rejected_without_external_write(self) -> None:
        plan = make_plan(status="draft")
        with self.assertRaisesMessage(InventoryApiError, "只有已确认"):
            sync_replenishment_plan(plan.id, "operator@example.test")

    def test_external_failure_is_recorded_without_changing_plan_status(self) -> None:
        plan = make_plan()

        class Gateway:
            target = load_target()

            def sync(self, _plan: ReplenishmentPlanItem) -> tuple[str, str]:
                raise InventoryApiError("钉钉测试失败", code="service_unavailable", status=503)

        with self.assertRaisesMessage(InventoryApiError, "钉钉测试失败"):
            sync_replenishment_plan(plan.id, "operator@example.test", gateway=Gateway())
        plan.refresh_from_db()

        self.assertEqual(plan.status, "confirmed")
        self.assertEqual(plan.dingtalk_sync_status, "failed")
        self.assertEqual(plan.dingtalk_sync_error, "钉钉测试失败")
        self.assertEqual(plan.dingtalk_record_id, "")

    def test_auth_failure_is_persisted_and_explicit_retry_can_succeed(self) -> None:
        plan = make_plan()
        fake = FakeDws()
        gateway = DingTalkReplenishmentGateway(fake.target, DwsCli(fake.target, fake))
        message = "钉钉登录授权已失效，请管理员为系统绑定的钉钉账号重新授权后，再重试原备货计划。"
        with patch.object(gateway, "sync", side_effect=InventoryApiError(message, status=503)) as attempt:
            with self.assertRaisesMessage(InventoryApiError, "登录授权已失效"):
                sync_replenishment_plan(plan.id, "operator@example.test", gateway=gateway)
            attempt.assert_called_once()
        plan.refresh_from_db()
        self.assertEqual(plan.status, "confirmed")
        self.assertEqual(plan.dingtalk_sync_error, message)
        self.assertEqual(plan.dingtalk_sync_status, "failed")
        self.assertEqual(plan.dingtalk_sync_owner_token, "")
        sync_replenishment_plan(plan.id, "operator@example.test", gateway=gateway)
        plan.refresh_from_db()
        self.assertEqual(plan.dingtalk_sync_status, "synced")
        self.assertEqual(plan.dingtalk_sync_error, "")

    def test_uncertain_create_is_reconciled_on_retry_without_duplicate_create(self) -> None:
        plan = make_plan()
        fake = FakeDws()

        def runner(args):
            result = fake(args)
            if args[:3] == ["aitable", "record", "create"]:
                raise InventoryApiError("钉钉请求超时", status=503)
            return result

        gateway = DingTalkReplenishmentGateway(fake.target, DwsCli(fake.target, runner))
        with self.assertRaisesMessage(InventoryApiError, "请求超时"):
            sync_replenishment_plan(plan.id, "operator@example.test", gateway=gateway)
        result = sync_replenishment_plan(plan.id, "operator@example.test", gateway=gateway)
        self.assertEqual(result["outcome"], "updated")
        self.assertEqual(sum(args[:3] == ["aitable", "record", "create"] for args in fake.commands), 1)
        self.assertEqual(sum(args[:3] == ["aitable", "record", "update"] for args in fake.commands), 1)

    def test_active_sync_lease_prevents_another_external_attempt(self) -> None:
        plan = make_plan()
        plan.dingtalk_sync_status = "syncing"
        plan.dingtalk_sync_owner_token = "existing-owner"
        plan.dingtalk_sync_started_at = timezone.now()
        plan.save()
        with patch("inventory.dingtalk_sync.DingTalkReplenishmentGateway") as gateway:
            with self.assertRaisesMessage(InventoryApiError, "正在同步"):
                sync_replenishment_plan(plan.id, "operator@example.test")
            gateway.assert_not_called()
        plan.refresh_from_db()
        self.assertEqual(plan.dingtalk_sync_owner_token, "existing-owner")

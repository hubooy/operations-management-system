from __future__ import annotations

import uuid
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from inventory.dingtalk_group_message import (
    DingTalkGroupGateway,
    build_group_preview,
    preview_group_message,
    send_group_message,
)
from inventory.errors import InventoryApiError
from inventory.models import (
    InventoryWriteAuthority,
    ReplenishmentGroupDelivery,
    ReplenishmentPlanItem,
)


class FakeGroupGateway:
    def __init__(self, *, fail_send: bool = False) -> None:
        self.fail_send = fail_send
        self.preflights: list[tuple[str, str, list[str]]] = []
        self.sends: list[dict[str, object]] = []

    def preflight(self, group_name: str, robot_name: str, buyers: list[str]):
        self.preflights.append((group_name, robot_name, buyers))
        return "group-1", "robot-1", [f"user-{buyer}" for buyer in buyers]

    def send(self, **kwargs):
        self.sends.append(kwargs)
        if self.fail_send:
            raise InventoryApiError("模拟发送结果不明确", code="service_unavailable", status=503)
        return {"ok": True, "result": {"success": True, "messageId": "message-1"}}


class FakeDwsCli:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, ...]] = []

    def run(self, *args: str) -> dict[str, object]:
        self.calls.append(args)
        return self.payload


def make_plan(code: str, name: str, buyer: str, supplier: str, quantity: int) -> ReplenishmentPlanItem:
    return ReplenishmentPlanItem.objects.create(
        id=str(uuid.uuid4()),
        source_batch_id="batch-1",
        product_code=code,
        product_name=name,
        supplier=supplier,
        warehouse="广东仓",
        buyer=buyer,
        current_stock_quantity=1,
        suggested_quantity=quantity,
        planned_quantity=quantity,
        status="confirmed",
    )


class DingTalkGroupMessageTests(TestCase):
    def setUp(self) -> None:
        InventoryWriteAuthority.objects.filter(id=1).update(
            status="postgres",
            authority_epoch=uuid.UUID("11111111-1111-4111-8111-111111111111"),
            cutover_id="inventory-test-cutover",
            migration_verify_run_id="inventory-apply-" + "1" * 32,
            activated_at=timezone.now(),
        )

    def test_preview_groups_by_buyer_and_supplier_with_exact_wording(self) -> None:
        first = make_plan("TRS-QRLS-400-007", "特睿思QRLS-400-7绞切机（2.2KW全不锈钢）", "采购甲", "百轮", 12)
        second = make_plan("TRS-BL-SXC-12-012", "特睿思SXC-12全钢款台式绞肉机（纯铜电机带把手带急停开关）1.3kw", "采购甲", "百轮", 4)
        third = make_plan("TRS-BL-TS-90-04", "特睿思TS-90台式脱卸切肉机3.5mm（1.1KW不锈钢外壳）", "采购甲", "百轮", 3)
        fourth = make_plan("TRS-MY-KC-17-01", "特睿思MY-KC-17型数控切菜机（快拆款）", "采购甲", "旻盈", 13)
        gateway = FakeGroupGateway()

        preview = preview_group_message(
            [third.id, first.id, fourth.id, second.id], "志高/特睿思备货计划群", "志高助手", gateway=gateway,
        )

        self.assertEqual(preview["targetGroupName"], "志高/特睿思备货计划群")
        self.assertEqual(preview["buyerNames"], ["采购甲"])
        self.assertEqual(preview["message"], "\n".join([
            "@采购甲  ",
            "**▸ 百轮（3 条）**  ",
            "TRS-BL-SXC-12-012 特睿思SXC-12全钢款台式绞肉机（纯铜电机带把手带急停开关）1.3kw，× 4台  ",
            "TRS-BL-TS-90-04 特睿思TS-90台式脱卸切肉机3.5mm（1.1KW不锈钢外壳），× 3台  ",
            "TRS-QRLS-400-007 特睿思QRLS-400-7绞切机（2.2KW全不锈钢），× 12台  ",
            "**▸ 旻盈（1 条）**  ",
            "TRS-MY-KC-17-01 特睿思MY-KC-17型数控切菜机（快拆款），× 13台",
        ]))
        self.assertRegex(str(preview["previewToken"]), r"^[0-9a-f]{64}$")
        self.assertNotIn("_groupId", preview)
        self.assertEqual(gateway.preflights, [("志高/特睿思备货计划群", "志高助手", ["采购甲"])])

    @patch("inventory.dingtalk_group_message.sync_replenishment_plan")
    def test_send_mentions_resolved_buyers_and_prevents_duplicate_delivery(self, sync_plan) -> None:
        plan = make_plan("P-001", "测试货品", "采购甲", "工厂甲", 8)
        gateway = FakeGroupGateway()
        preview = build_group_preview([plan.id], "志高/特睿思备货计划群", "志高助手", gateway=gateway)

        first = send_group_message(
            [plan.id], "志高/特睿思备货计划群", "志高助手", preview["previewToken"],
            "operator@example.test", gateway=gateway,
        )
        repeated = send_group_message(
            [plan.id], "志高/特睿思备货计划群", "志高助手", preview["previewToken"],
            "operator@example.test", gateway=gateway,
        )

        self.assertEqual(first["status"], "delivered")
        self.assertEqual(repeated["status"], "already_delivered")
        self.assertEqual(len(gateway.sends), 1)
        self.assertEqual(gateway.sends[0]["user_ids"], ["user-采购甲"])
        self.assertIn("@user-采购甲", gateway.sends[0]["message"])
        self.assertNotIn("@采购甲", gateway.sends[0]["message"])
        self.assertEqual(sync_plan.call_count, 2)
        delivery = ReplenishmentGroupDelivery.objects.get()
        self.assertEqual(delivery.status, "delivered")
        self.assertTrue(delivery.provider_receipt.startswith("dws-sha256:"))

    @patch("inventory.dingtalk_group_message.sync_replenishment_plan")
    def test_ambiguous_external_failure_is_fenced_as_uncertain(self, _sync_plan) -> None:
        plan = make_plan("P-002", "测试货品二", "采购乙", "工厂乙", 5)
        gateway = FakeGroupGateway(fail_send=True)
        preview = build_group_preview([plan.id], "志高/特睿思备货计划群", "志高助手", gateway=gateway)

        with self.assertRaisesMessage(InventoryApiError, "模拟发送结果不明确"):
            send_group_message(
                [plan.id], "志高/特睿思备货计划群", "志高助手", preview["previewToken"],
                "operator@example.test", gateway=gateway,
            )

        delivery = ReplenishmentGroupDelivery.objects.get()
        self.assertEqual(delivery.status, "uncertain")

    def test_draft_or_missing_business_identity_is_rejected_before_preflight(self) -> None:
        plan = make_plan("P-003", "测试货品三", "", "工厂丙", 2)
        gateway = FakeGroupGateway()

        with self.assertRaisesMessage(InventoryApiError, "对应采购"):
            preview_group_message([plan.id], "志高/特睿思备货计划群", "志高助手", gateway=gateway)
        self.assertEqual(gateway.preflights, [])

    def test_gateway_uses_markdown_mentions_and_accepts_batch_ledger(self) -> None:
        cli = FakeDwsCli({
            "contractVersion": "im.batch-write.v1",
            "requestedCount": 1,
            "succeededCount": 1,
            "failedCount": 0,
            "results": [{"status": "succeeded"}],
            "failures": [],
        })
        gateway = DingTalkGroupGateway.__new__(DingTalkGroupGateway)
        gateway.cli = cli

        gateway.send(
            group_id="group-1",
            robot_code="robot-1",
            message="@user-1\n\n**▸ 百轮（1 条）**\n货品，× 1台",
            user_ids=["user-1"],
        )

        command = cli.calls[0]
        self.assertIn("--groups", command)
        self.assertIn("--markdown", command)
        self.assertIn("--at-user-ids", command)
        self.assertNotIn("--text", command)

        cli.payload = {
            "contractVersion": "im.batch-write.v1",
            "requestedCount": 1,
            "succeededCount": 0,
            "failedCount": 1,
            "results": [{"status": "failed"}],
            "failures": [{"status": "failed"}],
        }
        with self.assertRaisesMessage(InventoryApiError, "回执未确认成功"):
            gateway.send(
                group_id="group-1",
                robot_code="robot-1",
                message="@user-1",
                user_ids=["user-1"],
            )

    def test_inventory_group_target_is_independent_from_weekly_report_target(self) -> None:
        plan = make_plan("P-004", "测试货品四", "采购甲", "工厂甲", 1)
        gateway = FakeGroupGateway()

        with self.assertRaisesMessage(InventoryApiError, "志高/特睿思备货计划群"):
            preview_group_message([plan.id], "测试群聊", "志高助手", gateway=gateway)
        self.assertEqual(gateway.preflights, [])

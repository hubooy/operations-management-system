from __future__ import annotations

import json
from urllib.parse import quote
from unittest.mock import patch

from django.test import TestCase

from sales.tests.factories import TEST_SECRET, signed_headers
from workflow.models import (
    WorkflowAttachmentCleanup,
    WorkflowOperationActivity,
    WorkflowOperationRecord,
    WorkflowOperationsWriteAuthority,
    WorkflowTask,
    WorkflowTaskActivityLog,
    WorkflowTaskAttachment,
    WorkflowWriteRequestReceipt,
)


def body_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


@patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
class WorkflowOperationsApiTests(TestCase):
    def test_record_priority_card_filters_before_pagination_and_preserves_scope(self):
        url = "/api/workflow/operations-records"
        for index, priority in enumerate(("normal", "high", "normal", "high")):
            response = self.request_json("POST", url, {
                "type": "inspection", "title": f"巡店 {index}", "status": "待处理", "priority": priority,
                "shopName": "京东一店" if index < 3 else "天猫二店", "owner": "运营组",
                "occurredAt": "2026-09-03T10:00:00+08:00",
            }, f"card-record-{index}")
            self.assertEqual(response.status_code, 201, response.content)
        response = self.signed_get(url + "?type=inspection&priority=high&pageSize=1", "card-priority")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["pagination"]["total"], 2)
        self.assertTrue(all(row["priority"] == "high" for row in response.json()["items"]))
        response = self.signed_get(url + "?type=inspection&priority=high&shopName=" + quote("京东一店"), "card-shop")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["pagination"]["total"], 1)
        self.assertEqual(self.signed_get(url + "?priority=unknown", "card-invalid").status_code, 400)

    def setUp(self) -> None:
        WorkflowOperationsWriteAuthority.objects.filter(id=1).update(status="postgres")

    def request_json(
        self,
        method: str,
        url: str,
        payload: dict[str, object],
        request_id: str,
        *,
        role: str = "admin",
        scope=None,
    ):
        body = body_bytes(payload)
        return getattr(self.client, method.lower())(
            url,
            data=body,
            content_type="application/json; charset=utf-8",
            headers=signed_headers(url, method=method, body=body, request_id=request_id, role=role, scope=scope),
        )

    def signed_get(self, url: str, request_id: str, *, role: str = "admin", scope=None):
        return self.client.get(url, headers=signed_headers(url, request_id=request_id, role=role, scope=scope))

    def create_task(self, request_id: str = "workflow-ops-task-create"):
        return self.request_json(
            "POST",
            "/api/workflow/tasks",
            {
                "title": "检查重点商品价格",
                "workContent": "核对活动价",
                "category": "活动运营",
                "owner": "运营组",
                "shopName": "京东一店",
                "startDate": "2026-09-03",
                "due": "2026-09-04",
                "priority": "high",
            },
            request_id,
        )

    def test_task_crud_filters_cas_and_replay(self) -> None:
        created = self.create_task()
        self.assertEqual(created.status_code, 201, created.content)
        item = created.json()["item"]
        self.assertEqual(item["version"], 1)
        self.assertEqual(item["source"], "手动录入")
        self.assertEqual(WorkflowTaskActivityLog.objects.filter(action="task.created").count(), 1)

        replay = self.create_task()
        self.assertEqual(replay.status_code, 201, replay.content)
        self.assertEqual(replay["X-Teruisi-Write-Replay"], "1")
        self.assertEqual(WorkflowTask.objects.count(), 1)

        listed_url = "/api/workflow/tasks?status=%E5%BE%85%E5%BC%80%E5%A7%8B&priority=high&page=1&pageSize=20"
        listed = self.signed_get(listed_url, "workflow-ops-task-list", role="viewer")
        self.assertEqual(listed.status_code, 200, listed.content)
        self.assertEqual(listed.json()["pagination"]["total"], 1)
        self.assertEqual(listed.json()["summary"]["open"], 1)

        update_url = f"/api/workflow/tasks?id={item['id']}"
        updated = self.request_json(
            "PATCH", update_url, {"status": "工作中", "expectedVersion": 1}, "workflow-ops-task-update",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()["item"]["version"], 2)
        self.assertEqual(updated.json()["item"]["status"], "工作中")

        stale = self.request_json(
            "PATCH", update_url, {"status": "已完成", "expectedVersion": 1}, "workflow-ops-task-stale",
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["code"], "version_conflict")

        delete_url = f"/api/workflow/tasks?id={item['id']}&expectedVersion=2"
        deleted = self.client.delete(delete_url, headers=signed_headers(delete_url, method="DELETE", request_id="workflow-ops-task-delete"))
        self.assertEqual(deleted.status_code, 200, deleted.content)
        self.assertTrue(deleted.json()["ok"])
        self.assertIsNotNone(WorkflowTask.objects.get(id=item["id"]).deleted_at)

    def test_task_collaboration_template_and_attachment_metadata(self) -> None:
        task_id = self.create_task("workflow-ops-collab-task").json()["item"]["id"]
        comment_url = f"/api/workflow/tasks/{task_id}/comments"
        comment = self.request_json("POST", comment_url, {"content": "请今天复核"}, "workflow-ops-comment")
        self.assertEqual(comment.status_code, 201, comment.content)

        reminder_url = f"/api/workflow/tasks/{task_id}/reminders"
        reminder = self.request_json(
            "POST", reminder_url, {"remindAt": "2026-09-04T09:00:00+08:00", "note": "上午提醒"}, "workflow-ops-reminder",
        )
        self.assertEqual(reminder.status_code, 201, reminder.content)
        reminder_id = reminder.json()["item"]["id"]

        link_url = f"/api/workflow/tasks/{task_id}/links"
        link = self.request_json(
            "POST", link_url, {"entityType": "report", "entityId": "inventory:sku-1", "label": "库存报告", "url": "https://example.test/report"}, "workflow-ops-link",
        )
        self.assertEqual(link.status_code, 201, link.content)

        attachment_url = f"/api/workflow/tasks/{task_id}/attachments"
        attachment_id = "attachment-1"
        attached = self.request_json(
            "POST",
            attachment_url,
            {
                "id": attachment_id,
                "fileName": "核对表.xlsx",
                "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "sizeBytes": 128,
                "sha256": "a" * 64,
                "objectKey": f"workflow-attachments/{task_id}/{attachment_id}",
            },
            "workflow-ops-attachment",
        )
        self.assertEqual(attached.status_code, 201, attached.content)
        self.assertNotIn("objectKey", attached.json()["item"])

        metadata_url = f"/api/workflow/tasks/{task_id}/attachments/{attachment_id}"
        metadata = self.signed_get(metadata_url, "workflow-ops-attachment-read", role="viewer")
        self.assertEqual(metadata.status_code, 200, metadata.content)
        self.assertEqual(metadata.json()["item"]["objectKey"], f"workflow-attachments/{task_id}/{attachment_id}")

        collab_url = f"/api/workflow/tasks/{task_id}/collaboration"
        collab = self.signed_get(collab_url, "workflow-ops-collab-read", role="viewer")
        self.assertEqual(collab.status_code, 200, collab.content)
        self.assertEqual(len(collab.json()["comments"]), 1)
        self.assertEqual(len(collab.json()["reminders"]), 1)
        self.assertEqual(len(collab.json()["links"]), 1)
        self.assertEqual(len(collab.json()["attachments"]), 1)

        dismiss_url = f"{reminder_url}?id={reminder_id}"
        dismissed = self.client.delete(dismiss_url, headers=signed_headers(dismiss_url, method="DELETE", request_id="workflow-ops-reminder-dismiss"))
        self.assertEqual(dismissed.status_code, 200, dismissed.content)

        deleted_attachment = self.client.delete(metadata_url, headers=signed_headers(metadata_url, method="DELETE", request_id="workflow-ops-attachment-delete"))
        self.assertEqual(deleted_attachment.status_code, 200, deleted_attachment.content)
        self.assertFalse(WorkflowTaskAttachment.objects.filter(id=attachment_id).exists())
        self.assertTrue(WorkflowAttachmentCleanup.objects.filter(object_key=f"workflow-attachments/{task_id}/{attachment_id}").exists())

        cleanup_url = "/api/workflow/attachment-cleanup?limit=10"
        cleanup = self.signed_get(cleanup_url, "workflow-ops-cleanup-read")
        self.assertEqual(cleanup.status_code, 200, cleanup.content)
        self.assertEqual(cleanup.json()["items"][0]["objectKey"], f"workflow-attachments/{task_id}/{attachment_id}")
        acknowledged = self.request_json(
            "POST", "/api/workflow/attachment-cleanup", {"objectKey": f"workflow-attachments/{task_id}/{attachment_id}", "deleted": True}, "workflow-ops-cleanup-result",
        )
        self.assertTrue(acknowledged.json()["removed"])

        template = self.request_json(
            "POST", "/api/workflow/templates", {"name": "巡店模板", "title": "每日巡店", "startOffsetDays": 0, "dueOffsetDays": 1}, "workflow-ops-template",
        )
        self.assertEqual(template.status_code, 201, template.content)
        template_item = template.json()["item"]
        updated_template_url = f"/api/workflow/templates?id={template_item['id']}"
        updated_template = self.request_json(
            "PATCH", updated_template_url, {"active": False, "expectedVersion": 1}, "workflow-ops-template-update",
        )
        self.assertEqual(updated_template.json()["item"]["version"], 2)

    def test_operation_records_enforce_or_scope_and_audit(self) -> None:
        create_url = "/api/workflow/operations-records"
        invalid_due = self.request_json(
            "POST",
            create_url,
            {
                "type": "inspection", "title": "时间边界", "platform": "京东", "shopName": "京东一店",
                "occurredAt": "2026-09-03T10:00:00+08:00", "dueAt": "2026-09-03T09:59:59+08:00",
            },
            "workflow-ops-record-invalid-due",
            role="operator",
        )
        self.assertEqual(invalid_due.status_code, 400, invalid_due.content)
        self.assertIn("截止时间不能早于发生时间", invalid_due.json()["error"])
        self.assertEqual(WorkflowOperationRecord.objects.count(), 0)

        record = self.request_json(
            "POST",
            create_url,
            {
                "type": "inspection", "title": "京东巡店", "status": "待处理", "priority": "high",
                "platform": "京东", "channel": "线上", "shopName": "京东一店", "owner": "运营组",
                "occurredAt": "2026-09-03T10:00:00+08:00", "content": "发现价格异常",
            },
            "workflow-ops-record",
            role="operator",
        )
        self.assertEqual(record.status_code, 201, record.content)
        item = record.json()["item"]
        self.assertEqual(WorkflowOperationActivity.objects.filter(record_id=item["id"], action="created").count(), 1)

        allowed_scope = {"warehouses": [], "channels": [], "platforms": ["京东"]}
        allowed = self.signed_get(create_url, "workflow-ops-record-list-allowed", role="viewer", scope=allowed_scope)
        self.assertEqual(allowed.status_code, 200, allowed.content)
        self.assertEqual(allowed.json()["pagination"]["total"], 1)
        self.assertEqual(allowed.json()["filtersApplied"]["dataScope"], "restricted")

        denied_scope = {"warehouses": [], "channels": [], "platforms": ["天猫"]}
        denied = self.signed_get(create_url, "workflow-ops-record-list-denied", role="viewer", scope=denied_scope)
        self.assertEqual(denied.status_code, 200, denied.content)
        self.assertEqual(denied.json()["pagination"]["total"], 0)

        forbidden_write = self.request_json(
            "POST",
            create_url,
            {"type": "review", "title": "评价", "shopName": "京东一店", "platform": "京东", "occurredAt": "2026-09-03T10:00:00+08:00"},
            "workflow-ops-record-forbidden",
            role="operator",
            scope=denied_scope,
        )
        self.assertEqual(forbidden_write.status_code, 403)

        item_url = f"/api/workflow/operations-records/{item['id']}"
        updated = self.request_json(
            "PATCH", item_url, {"status": "处理中", "expectedVersion": 1}, "workflow-ops-record-update", role="operator",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()["item"]["version"], 2)
        self.assertEqual(WorkflowOperationActivity.objects.filter(record_id=item["id"], action="status_changed").count(), 1)

        activity_url = f"{item_url}/activity?page=1&pageSize=10"
        activity = self.signed_get(activity_url, "workflow-ops-record-activity", role="viewer", scope=allowed_scope)
        self.assertEqual(activity.status_code, 200, activity.content)
        self.assertEqual(activity.json()["pagination"]["total"], 2)

    def test_inventory_work_item_is_atomic_and_idempotent(self) -> None:
        url = "/api/workflow/inventory-work-items"
        payload = {
            "entityId": "inventory:replenishment:sku-1",
            "label": "SKU-1 补货建议",
            "url": "",
            "title": "处理 SKU-1 补货建议",
            "workContent": "核对库存和销量",
            "category": "库存管理",
            "owner": "运营组",
            "shopName": "未关联店铺",
            "startDate": "待排期",
            "due": "待排期",
            "priority": "high",
        }
        first = self.request_json("POST", url, payload, "workflow-ops-inventory-first")
        self.assertEqual(first.status_code, 201, first.content)
        self.assertTrue(first.json()["created"])
        second = self.request_json("POST", url, payload, "workflow-ops-inventory-second")
        self.assertEqual(second.status_code, 201, second.content)
        self.assertFalse(second.json()["created"])
        self.assertEqual(first.json()["task"]["id"], second.json()["task"]["id"])
        self.assertEqual(WorkflowTask.objects.count(), 1)

    def test_workflow_search_consumer_combines_unscoped_data_and_honors_record_scope(self) -> None:
        created_task = self.request_json(
            "POST",
            "/api/workflow/tasks",
            {"title": "检索词任务", "workContent": "检索词内容"},
            "workflow-search-task",
        )
        self.assertEqual(created_task.status_code, 201, created_task.content)
        for suffix, platform in (("jd", "京东"), ("tmall", "天猫")):
            created_record = self.request_json(
                "POST",
                "/api/workflow/operations-records",
                {
                    "type": "inspection",
                    "title": f"检索词{platform}巡店",
                    "platform": platform,
                    "shopName": f"{platform}一店",
                    "occurredAt": "2026-09-03T10:00:00+08:00",
                },
                f"workflow-search-record-{suffix}",
                role="operator",
            )
            self.assertEqual(created_record.status_code, 201, created_record.content)

        consumer_url = "/api/workflow/consumers/query"
        unrestricted = self.request_json(
            "POST",
            consumer_url,
            {"operation": "workflow_search", "query": "检索词", "offset": 0, "limit": 10},
            "workflow-search-unrestricted",
            role="viewer",
        )
        self.assertEqual(unrestricted.status_code, 200, unrestricted.content)
        unrestricted_data = unrestricted.json()["data"]
        self.assertEqual(unrestricted_data["total"], 3)
        self.assertEqual(
            {item["targetHint"] for item in unrestricted_data["items"]},
            {"task", "inspection"},
        )

        scoped = self.request_json(
            "POST",
            consumer_url,
            {"operation": "workflow_search", "query": "检索词", "offset": 0, "limit": 10},
            "workflow-search-scoped",
            role="viewer",
            scope={"warehouses": [], "channels": [], "platforms": ["京东"]},
        )
        self.assertEqual(scoped.status_code, 200, scoped.content)
        scoped_data = scoped.json()["data"]
        self.assertEqual(scoped_data["total"], 1)
        self.assertEqual(scoped_data["items"][0]["targetHint"], "inspection")
        self.assertIn("京东", scoped_data["items"][0]["title"])

        scoped_launch = self.request_json(
            "POST",
            consumer_url,
            {"operation": "launch_project_search", "query": "检索词", "offset": 0, "limit": 10},
            "workflow-search-scoped-launch",
            role="viewer",
            scope={"warehouses": [], "channels": [], "platforms": ["京东"]},
        )
        self.assertEqual(scoped_launch.status_code, 403, scoped_launch.content)
        self.assertEqual(scoped_launch.json()["code"], "access_denied")

    def test_operations_authority_and_receipt_fail_closed(self) -> None:
        WorkflowOperationsWriteAuthority.objects.filter(id=1).update(status="disabled")
        response = self.create_task("workflow-ops-disabled")
        self.assertEqual(response.status_code, 503, response.content)
        self.assertEqual(response.json()["code"], "workflow_operations_write_authority_inactive")
        self.assertFalse(WorkflowWriteRequestReceipt.objects.filter(request_id="workflow-ops-disabled").exists())
        self.assertEqual(WorkflowTask.objects.count(), 0)

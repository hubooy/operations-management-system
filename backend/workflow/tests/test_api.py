from __future__ import annotations

import json
import uuid
from unittest.mock import patch
from urllib.parse import quote

from django.test import TestCase, override_settings

from sales.tests.factories import TEST_SECRET, signed_headers
from workflow.models import (
    NewProductActivity,
    NewProductProject,
    WorkflowOperationsWriteAuthority,
    WorkflowWriteAuthority,
)


def body_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def project_payload() -> dict[str, object]:
    return {
        "productName": "大通量商用净水器",
        "supplierName": "供应商甲",
        "brand": "志高",
        "category": "商用净水",
        "erpProductCode": "ERP-NEW-001",
        "skuCode": "SKU-NEW-001",
        "spuCode": "SPU-NEW-001",
        "productImageUrl": "https://example.test/product.jpg",
        "proposedBy": "商品经理",
        "proposedDate": "2026-09-02",
        "owner": "新品负责人",
        "targetLaunchDate": "2026-09-16",
        "lifecycleStatus": "active",
        "priority": "high",
        "recommendedPriceCents": 399_900,
        "approvedPriceCents": None,
        "estimatedGrossMarginBps": 3_200,
        "source": "manual",
        "sourceRef": "",
        "notes": "内部项目说明不应写入活动详情",
        "targets": [
            {
                "platform": "京东",
                "shopName": "志高商用设备旗舰店",
                "channel": "线上",
                "listingSku": "JD-001",
                "listingUrl": "",
                "status": "pending",
            },
            {
                "platform": "天猫",
                "shopName": "志高亿用专卖店",
                "channel": "线上",
                "listingSku": "TM-001",
                "listingUrl": "",
                "status": "ready",
            },
        ],
    }


@patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
class WorkflowApiContractTests(TestCase):
    def setUp(self) -> None:
        WorkflowWriteAuthority.objects.filter(id=1).update(status="postgres")

    def request_json(
        self,
        method: str,
        url: str,
        payload: dict[str, object],
        request_id: str,
        *,
        role: str = "admin",
    ):
        body = body_bytes(payload)
        return getattr(self.client, method.lower())(
            url,
            data=body,
            content_type="application/json; charset=utf-8",
            headers=signed_headers(
                url,
                method=method,
                body=body,
                request_id=request_id,
                role=role,
            ),
        )

    def create_project(self, request_id: str = "workflow-create-1"):
        return self.request_json("POST", "/api/workflow/launch-projects", project_payload(), request_id)

    def test_free_text_shop_plan_can_start_blank_and_preserves_legacy_targets(self):
        payload = project_payload()
        payload.pop("targets")
        payload["shopPlan"] = ""
        response = self.request_json("POST", "/api/workflow/launch-projects", payload, "text-create")
        self.assertEqual(response.status_code, 201, response.content)
        created = response.json()["item"]
        self.assertEqual(created["shopPlan"], "")
        self.assertEqual(created["targets"], [])
        self.assertEqual(len(created["stages"]), 7)
        legacy = self.create_project("legacy-targets").json()["item"]
        self.assertIsNone(legacy["shopPlan"])
        url = f"/api/workflow/launch-projects/{legacy['id']}"
        text = "京东：先上旗舰店\n天猫：图片确认后再上架"
        response = self.request_json("PATCH", url, {"expectedVersion": 1, "shopPlan": text, "notes": "等供应商确认\n下周跟进"}, "text-update")
        self.assertEqual(response.status_code, 200, response.content)
        item = response.json()["item"]
        self.assertEqual(item["shopPlan"], text)
        self.assertEqual(item["targets"], legacy["targets"])
        self.assertEqual(item["notes"], "等供应商确认\n下周跟进")
        reread = self.client.get(url, headers=signed_headers(url)).json()["item"]
        self.assertEqual(reread["shopPlan"], text)
        conflict = self.request_json("PATCH", url, {"expectedVersion": 1, "shopPlan": "覆盖"}, "text-conflict")
        self.assertEqual(conflict.status_code, 409)
        cleared = self.request_json("PATCH", url, {"expectedVersion": 2, "shopPlan": ""}, "text-clear")
        self.assertEqual(cleared.status_code, 200, cleared.content)
        self.assertEqual(cleared.json()["item"]["shopPlan"], "")
        self.assertEqual(cleared.json()["item"]["targets"], legacy["targets"])
        invalid = self.request_json("PATCH", url, {"expectedVersion": 3, "shopPlan": "长" * 4001}, "text-limit")
        self.assertEqual(invalid.status_code, 400)
        denied = self.request_json("PATCH", url, {"expectedVersion": 3, "shopPlan": "越权"}, "text-viewer", role="viewer")
        self.assertEqual(denied.status_code, 403)
        activity = NewProductActivity.objects.filter(project_id=legacy["id"], action="project.updated").first()
        self.assertIn("shopPlan", activity.changed_fields)

    def test_status_and_overdue_filters_apply_before_pagination(self):
        from datetime import timedelta
        from django.utils import timezone
        from workflow.models import NewProductStage
        first = self.create_project("quick-first").json()["item"]
        self.create_project("quick-second")
        NewProductStage.objects.filter(project_id=first["id"], stage_key="modeling").update(
            status="in_progress", planned_due_date=timezone.localdate() - timedelta(days=1))
        for query in ("status=in_progress", "overdue=true", "status=in_progress&overdue=true"):
            url = "/api/workflow/launch-projects?" + query + "&page=1&pageSize=1"
            response = self.client.get(url, headers=signed_headers(url))
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["pagination"]["total"], 1)
            self.assertEqual(response.json()["quickSummary"]["total"], 2)
            self.assertEqual(response.json()["items"][0]["id"], first["id"])
        url = "/api/workflow/launch-projects?overdue=invalid"
        self.assertEqual(self.client.get(url, headers=signed_headers(url)).status_code, 400)
        url = "/api/workflow/launch-projects?pageSize=1"
        response = self.client.get(url, headers=signed_headers(url))
        self.assertEqual(response.json()["pagination"]["total"], 2)
        self.assertTrue(response.json()["pagination"]["truncated"])

    def test_workflow_reader_readiness_validates_schema_indexes_and_revision(self) -> None:
        self.assertEqual(self.create_project("workflow-ready-reader").status_code, 201)
        with override_settings(DJANGO_PROCESS_ROLE="workflow_reader", DJANGO_EXPECT_READ_ONLY=False):
            response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["workflowReader"], "ready")

    def test_workflow_writer_readiness_validates_authority_and_schema(self) -> None:
        self.assertEqual(self.create_project("workflow-ready-writer").status_code, 201)
        authority_epoch = uuid.uuid4()
        operations_epoch = uuid.uuid4()
        cutover_id = "workflow-ready-cutover"
        operations_cutover_id = "workflow-operations-ready-cutover"
        WorkflowWriteAuthority.objects.filter(id=1).update(
            authority_epoch=authority_epoch,
            cutover_id=cutover_id,
            migration_verify_run_id="workflow-" + "a" * 32,
        )
        WorkflowOperationsWriteAuthority.objects.filter(id=1).update(
            status="postgres",
            authority_epoch=operations_epoch,
            cutover_id=operations_cutover_id,
            migration_verify_run_id="workflow-ops-" + "b" * 32,
        )
        with override_settings(
            DJANGO_PROCESS_ROLE="workflow_writer",
            DJANGO_EXPECT_READ_ONLY=False,
            WORKFLOW_WRITE_AUTHORITY_EPOCH=str(authority_epoch),
            WORKFLOW_WRITE_CUTOVER_ID=cutover_id,
            WORKFLOW_OPERATIONS_WRITE_AUTHORITY_EPOCH=str(operations_epoch),
            WORKFLOW_OPERATIONS_WRITE_CUTOVER_ID=operations_cutover_id,
        ):
            # The test owner has DDL rights; exercise the writer contract as a
            # real restricted role instead of weakening the production guard.
            from django.db import connection
            if connection.vendor == "postgresql":
                from psycopg import sql
                from teruisi_backend.health import WORKFLOW_WRITER_TABLE_PRIVILEGES
                role = "wf_writer_test_" + uuid.uuid4().hex[:12]
                with connection.cursor() as cursor:
                    cursor.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
                    cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
                    for table, privileges in WORKFLOW_WRITER_TABLE_PRIVILEGES.items():
                        cursor.execute(sql.SQL("GRANT {} ON TABLE {} TO {}").format(
                            sql.SQL(", ").join(sql.SQL(value) for value in privileges), sql.Identifier(table), sql.Identifier(role)))
                    cursor.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
                    try:
                        response = self.client.get("/health/ready")
                    finally:
                        cursor.execute("RESET ROLE")
            else:
                response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["workflowWriter"], "ready")

    def test_project_create_list_filters_and_seven_stage_contract(self) -> None:
        created = self.create_project()
        self.assertEqual(created.status_code, 201, created.content)
        item = created.json()["item"]
        self.assertEqual(item["version"], 1)
        self.assertEqual(item["status"], "not_started")
        self.assertEqual(item["progressPercent"], 0)
        self.assertEqual(len(item["targets"]), 2)
        self.assertEqual(
            [stage["stageKey"] for stage in item["stages"]],
            ["modeling", "pricing", "image", "video", "listing", "stocking", "review"],
        )
        self.assertEqual(item["stages"][-1]["plannedDueDate"], "2026-09-23")
        self.assertTrue(created["X-Workflow-Data-Revision"].startswith("1:"))

        url = (
            "/api/workflow/launch-projects?"
            f"supplier={quote('供应商甲')}&platform={quote('京东')}&shopName={quote('志高商用设备旗舰店')}"
            "&status=not_started&page=1&pageSize=20"
        )
        listed = self.client.get(url, headers=signed_headers(url, request_id="workflow-list-1"))
        self.assertEqual(listed.status_code, 200, listed.content)
        payload = listed.json()
        self.assertEqual(payload["pagination"]["total"], 1)
        self.assertEqual(payload["summary"]["notStarted"], 1)
        self.assertEqual(payload["summary"]["stageSummary"][0]["not_started"], 1)
        self.assertIn("供应商甲", payload["facets"]["suppliers"])

        consumer_url = "/api/workflow/consumers/query"
        consumer = self.request_json(
            "POST",
            consumer_url,
            {"operation": "launch_project_search", "query": "净水器", "offset": 0, "limit": 10},
            "workflow-consumer-1",
            role="viewer",
        )
        self.assertEqual(consumer.status_code, 200, consumer.content)
        result = consumer.json()
        self.assertEqual(result["operation"], "launch_project_search")
        self.assertEqual(result["data"]["total"], 1)
        self.assertEqual(result["data"]["items"][0]["title"], "大通量商用净水器")
        self.assertNotIn("stages", result["data"]["items"][0])

    def test_stage_updates_allow_empty_optional_details_and_use_cas(self) -> None:
        item = self.create_project("workflow-create-stage").json()["item"]
        stage = next(stage for stage in item["stages"] if stage["stageKey"] == "pricing")
        url = f"/api/workflow/launch-projects/{item['id']}/stages/pricing"
        updated = self.request_json(
            "PATCH",
            url,
            {
                "status": "blocked",
                "owner": "定价负责人",
                "plannedDueDate": "2026-09-06",
                "blocker": "",
                "notes": "这里包含业务详情",
                "evidenceUrl": "https://example.test/pricing.xlsx",
                "evidenceLabel": "定价测算表",
                "expectedVersion": stage["version"],
            },
            "workflow-stage-update",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        result = updated.json()["item"]
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["version"], 2)
        pricing = next(value for value in result["stages"] if value["stageKey"] == "pricing")
        self.assertEqual(pricing["version"], 2)
        self.assertEqual(pricing["blocker"], "")

        stale = self.request_json(
            "PATCH",
            url,
            {"status": "completed", "expectedVersion": stage["version"]},
            "workflow-stage-stale",
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["code"], "version_conflict")
        activity = NewProductActivity.objects.filter(project_id=item["id"], action="stage.updated").get()
        self.assertIn("notes", activity.changed_fields)
        self.assertNotIn("这里包含业务详情", json.dumps(activity.changed_fields, ensure_ascii=False))

    def test_create_accepts_blocked_stage_without_blocker(self) -> None:
        payload = project_payload()
        payload["stages"] = [{"stageKey": "pricing", "status": "blocked"}]
        created = self.request_json("POST", "/api/workflow/launch-projects", payload, "workflow-create-blocked-stage")
        self.assertEqual(created.status_code, 201, created.content)
        pricing = next(stage for stage in created.json()["item"]["stages"] if stage["stageKey"] == "pricing")
        self.assertEqual(pricing["status"], "blocked")
        self.assertEqual(pricing["blocker"], "")

    def test_project_update_replaces_target_set_and_write_replay_is_fenced(self) -> None:
        created_response = self.create_project("workflow-create-replay")
        replay = self.create_project("workflow-create-replay")
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay["X-Teruisi-Write-Replay"], "1")
        self.assertEqual(NewProductProject.objects.count(), 1)
        item = created_response.json()["item"]
        url = f"/api/workflow/launch-projects/{item['id']}"
        updated = self.request_json(
            "PATCH",
            url,
            {
                "approvedPriceCents": 379_900,
                "targets": [
                    {
                        "platform": "京东",
                        "shopName": "志高商用设备旗舰店",
                        "channel": "线上",
                        "listingSku": "JD-001",
                        "listingUrl": "https://example.test/jd/1",
                        "status": "listed",
                    }
                ],
                "expectedVersion": item["version"],
            },
            "workflow-project-update",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        result = updated.json()["item"]
        self.assertEqual(result["approvedPriceCents"], 379_900)
        self.assertEqual(len(result["targets"]), 1)
        self.assertEqual(result["targets"][0]["status"], "listed")

        collision_payload = project_payload()
        collision_payload["productName"] = "另一个项目"
        collision = self.request_json(
            "POST", "/api/workflow/launch-projects", collision_payload, "workflow-create-replay"
        )
        self.assertEqual(collision.status_code, 409)
        self.assertEqual(collision.json()["code"], "version_conflict")

    def test_permissions_scope_dates_and_delete_are_fail_closed(self) -> None:
        integration = project_payload()
        integration["source"] = "integration"
        forbidden = self.request_json(
            "POST", "/api/workflow/launch-projects", integration, "workflow-source-forbidden", role="operator"
        )
        self.assertEqual(forbidden.status_code, 403)

        bad_date = project_payload()
        bad_date["targetLaunchDate"] = "2026-09-01"
        invalid = self.request_json(
            "POST", "/api/workflow/launch-projects", bad_date, "workflow-bad-date"
        )
        self.assertEqual(invalid.status_code, 400)

        scope = {"warehouses": [], "channels": [], "platforms": ["京东"]}
        url = "/api/workflow/launch-projects"
        restricted = self.client.get(
            url,
            headers=signed_headers(url, scope=scope, request_id="workflow-restricted"),
        )
        self.assertEqual(restricted.status_code, 403)
        self.assertEqual(restricted.json()["code"], "access_denied")

        item = self.create_project("workflow-create-delete").json()["item"]
        delete_url = f"/api/workflow/launch-projects/{item['id']}?expectedVersion={item['version']}"
        deleted = self.client.delete(
            delete_url,
            headers=signed_headers(delete_url, method="DELETE", request_id="workflow-delete"),
        )
        self.assertEqual(deleted.status_code, 200, deleted.content)
        self.assertEqual(deleted.json()["ok"], True)
        self.assertEqual(NewProductProject.objects.filter(deleted_at__isnull=True).count(), 0)

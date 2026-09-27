from __future__ import annotations

import copy
import hashlib
import json
from unittest.mock import patch
from urllib.parse import quote

from django.test import TestCase

from finance.models import (
    FinanceImportAttempt,
    FinanceImportBatch,
    FinanceLine,
    FinanceMonth,
    FinanceTarget,
    FinanceTargetDeletionAudit,
    FinanceWriteAuthority,
)
from sales.tests.factories import TEST_SECRET, signed_headers

from .factories import body_bytes, changed_raw_file, prepared_payload


class FinanceApiContractTests(TestCase):
    def setUp(self) -> None:
        FinanceWriteAuthority.objects.filter(id=1).update(status="postgres")

    def post_json(self, url: str, payload: dict[str, object], request_id: str):
        body = body_bytes(payload)
        return self.client.post(
            url,
            data=body,
            content_type="application/json; charset=utf-8",
            headers=signed_headers(url, method="POST", body=body, request_id=request_id),
        )

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_import_is_atomic_content_idempotent_and_replay_fenced(self) -> None:
        payload = prepared_payload("2026-07", "2026-08")
        first = self.post_json("/api/finance/imports", payload, "finance-import-1")
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(first.json()["status"], "imported")
        self.assertEqual(FinanceMonth.objects.count(), 2)
        expected_rows = sum(len(month["lines"]) for month in payload["months"])
        self.assertEqual(FinanceLine.objects.count(), expected_rows)
        self.assertEqual(FinanceImportBatch.objects.count(), 1)

        replay = self.post_json("/api/finance/imports", payload, "finance-import-1")
        self.assertEqual(replay.status_code, 201, replay.content)
        self.assertEqual(replay["X-Teruisi-Write-Replay"], "1")
        self.assertEqual(FinanceImportBatch.objects.count(), 1)

        duplicate_payload = changed_raw_file(payload)
        duplicate = self.post_json("/api/finance/imports", duplicate_payload, "finance-import-2")
        self.assertEqual(duplicate.status_code, 200, duplicate.content)
        self.assertEqual(duplicate.json()["status"], "duplicate")
        self.assertEqual(FinanceImportBatch.objects.count(), 1)
        self.assertEqual(FinanceImportAttempt.objects.filter(outcome="duplicate").count(), 1)

        collision_payload = copy.deepcopy(payload)
        collision_payload["rawFileHash"] = hashlib.sha256(b"collision").hexdigest()
        collision_body = body_bytes(collision_payload)
        collision = self.client.post(
            "/api/finance/imports",
            data=collision_body,
            content_type="application/json",
            headers=signed_headers(
                "/api/finance/imports",
                method="POST",
                body=collision_body,
                request_id="finance-import-1",
            ),
        )
        self.assertEqual(collision.status_code, 409)
        self.assertEqual(collision.json()["code"], "version_conflict")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_changed_month_replaces_exact_authoritative_set(self) -> None:
        original = prepared_payload("2026-08")
        first = self.post_json("/api/finance/imports", original, "replace-1")
        old_batch = first.json()["batch"]["id"]
        changed = copy.deepcopy(original)
        changed["rawFileHash"] = hashlib.sha256(b"changed").hexdigest()
        changed["months"][0]["lines"][2]["amountCents"] = 123_456
        second = self.post_json("/api/finance/imports", changed, "replace-2")
        self.assertEqual(second.status_code, 201, second.content)
        self.assertNotEqual(second.json()["batch"]["id"], old_batch)
        self.assertEqual(
            FinanceLine.objects.get(month="2026-08", scope_key="business", metric_key="net_sales").amount_cents,
            123_456,
        )
        self.assertEqual(FinanceMonth.objects.get(month="2026-08").batch_id, second.json()["batch"]["id"])
        self.assertEqual(FinanceImportBatch.objects.count(), 2)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_analysis_preserves_metrics_filters_and_same_name_shop_identity(self) -> None:
        imported = self.post_json(
            "/api/finance/imports", prepared_payload("2026-07", "2026-08"), "analysis-import"
        )
        self.assertEqual(imported.status_code, 201)
        url = "/api/finance/analysis?month=2026-08"
        response = self.client.get(url, headers=signed_headers(url))
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["current"]["netSalesCents"], 200_000)
        self.assertEqual(payload["current"]["grossMarginBps"], 4_000)
        self.assertEqual(payload["current"]["promotionExpenseCents"], 12_000)
        self.assertEqual(payload["previous"]["netSalesCents"], 100_000)
        self.assertEqual(len(payload["shops"]), 2)
        self.assertEqual({shop["groupName"] for shop in payload["shops"]}, {"京东", "天猫"})
        self.assertTrue(response["X-Finance-Data-Revision"].startswith("1:"))

        shop_key = quote(json.dumps(["京东", "同名店"], ensure_ascii=False, separators=(",", ":")))
        filtered_url = f"/api/finance/analysis?month=2026-08&shop={shop_key}"
        filtered = self.client.get(filtered_url, headers=signed_headers(filtered_url))
        self.assertEqual(filtered.status_code, 200, filtered.content)
        self.assertEqual(filtered.json()["current"]["netSalesCents"], 120_000)
        self.assertEqual(filtered.json()["selection"]["shops"], [json.dumps(["京东", "同名店"], ensure_ascii=False, separators=(",", ":"))])

        bad_key = quote(json.dumps(["拼多多", "同名店"], ensure_ascii=False, separators=(",", ":")))
        bad_url = f"/api/finance/analysis?month=2026-08&shop={bad_key}"
        bad = self.client.get(bad_url, headers=signed_headers(bad_url))
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.json()["code"], "finance_dimension_filter_out_of_scope")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_target_create_update_delete_use_cas_and_audit(self) -> None:
        create_payload = {
            "periodType": "month",
            "periodKey": "2026-08",
            "platform": "京东",
            "shopName": "同名店",
            "category": "饮水设备",
            "manager": "张三",
            "salesTargetCents": 500_000,
            "profitTargetCents": 100_000,
            "smallMarginBps": 2_000,
            "inventoryCleanupTargetCents": 0,
            "promotionFeeRatioBps": 800,
            "stagnantInventoryTargetCents": 0,
        }
        created = self.post_json("/api/finance/targets", create_payload, "target-create")
        self.assertEqual(created.status_code, 201, created.content)
        target = created.json()["item"]
        self.assertEqual(target["version"], 1)

        update_payload = {**create_payload, "id": target["id"], "expectedVersion": 1, "salesTargetCents": 600_000}
        updated = self.post_json("/api/finance/targets", update_payload, "target-update")
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()["item"]["version"], 2)

        stale = self.post_json("/api/finance/targets", update_payload, "target-stale")
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["code"], "version_conflict")

        delete_url = f"/api/finance/targets?id={quote(target['id'])}&expectedVersion=2&reason={quote('目标调整')}"
        deleted = self.client.delete(
            delete_url,
            headers=signed_headers(delete_url, method="DELETE", request_id="target-delete"),
        )
        self.assertEqual(deleted.status_code, 200, deleted.content)
        self.assertFalse(FinanceTarget.objects.filter(id=target["id"]).exists())
        audit = FinanceTargetDeletionAudit.objects.get(target_id=target["id"])
        self.assertEqual(audit.old_version, 2)
        self.assertEqual(audit.actor, "admin@example.test")

        replay = self.client.delete(
            delete_url,
            headers=signed_headers(delete_url, method="DELETE", request_id="target-delete"),
        )
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(replay["X-Teruisi-Write-Replay"], "1")
        altered_url = delete_url.replace(quote("目标调整"), quote("另一个原因"))
        collision = self.client.delete(
            altered_url,
            headers=signed_headers(altered_url, method="DELETE", request_id="target-delete"),
        )
        self.assertEqual(collision.status_code, 409, collision.content)
        self.assertEqual(collision.json()["code"], "version_conflict")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_rejected_attempt_is_audited_without_claiming_scope_or_creating_batch(self) -> None:
        rejected = {
            "schemaVersion": "finance-normalized-v1",
            "disposition": "rejected",
            "fileName": "坏文件.xlsx",
            "fileSizeBytes": 20,
            "rawFileHash": hashlib.sha256(b"bad").hexdigest(),
            "warnings": [],
            "errors": [{"code": "FINANCE_PARSE_ERROR", "message": "解析失败"}],
            "message": "月度财报解析失败，请确认文件格式和模板",
        }
        response = self.post_json("/api/finance/imports", rejected, "reject-1")
        self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(FinanceImportBatch.objects.count(), 0)
        attempt = FinanceImportAttempt.objects.get()
        self.assertEqual(attempt.outcome, "rejected")
        self.assertEqual(attempt.scope_key, "")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_finance_endpoints_fail_closed_for_restricted_principals(self) -> None:
        scope = {"warehouses": [], "channels": [], "platforms": ["京东"]}
        url = "/api/finance/analysis"
        response = self.client.get(url, headers=signed_headers(url, scope=scope))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "access_denied")

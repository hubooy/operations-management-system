from __future__ import annotations

import copy
import hashlib
import json
from unittest.mock import patch

from django.test import TestCase

from customer_service.models import CustomerServiceConversation, CustomerServiceDataRevision, CustomerServiceWriteAuthority
from sales.tests.factories import TEST_SECRET, signed_headers


def body_for(*, raw_hash: str = "a" * 64) -> dict[str, object]:
    return {
        "action": "import", "shopName": "志高商用设备", "sessionFileName": "session.xlsx", "chatFileName": "chat.log",
        "rawFileHash": raw_hash, "fileSizeBytes": 2048,
        "summary": {"sessionCount": 1, "chatSessionCount": 1, "matchedCount": 1, "timeOnlyMatchedCount": 0, "sessionOnlyCount": 0, "chatOnlyCount": 0, "ambiguousCount": 0},
        "warnings": [], "warningTotalCount": 0,
        "conversations": [{
            "sourceRowNumber": 2, "consultedAt": "2026-09-01 10:00:00", "customerId": "customer-1", "customerAlias": "顾客1",
            "consultationType": "商品咨询", "agent": "客服A", "transferredAgent": "", "skillGroup": "在线客服",
            "productSku": "SKU-1", "productName": "饮水机", "firstResponseAt": "2026-09-01 10:00:02", "responseSeconds": 2,
            "durationMinutes": 3.5, "customerMessageCount": 1, "agentMessageCount": 1, "satisfaction": "满意", "resolved": "已解决",
            "conversationId": "chat-1", "conversationKey": "conversation-1", "matchStatus": "matched", "matchConfidence": "exact",
            "chatStartedAt": "2026-09-01 10:00:00", "chatEndedAt": "2026-09-01 10:03:30", "chatCustomerAlias": "顾客1",
            "messages": [{"sender": "顾客", "sentAt": "2026-09-01 10:00:00", "content": "请问价格？"}],
        }],
    }


class CustomerServiceApiTests(TestCase):
    def setUp(self) -> None:
        CustomerServiceWriteAuthority.objects.filter(id=1).update(status="postgres")
        CustomerServiceDataRevision.objects.filter(domain="customer-service").update(revision=1, source_digest="a" * 64)

    def post(self, payload: dict[str, object], request_id: str):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self.client.post(
            "/api/customer-service/imports", data=body, content_type="application/json; charset=utf-8",
            headers=signed_headers("/api/customer-service/imports", method="POST", body=body, request_id=request_id),
        )

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_import_is_atomic_idempotent_and_replay_fenced(self) -> None:
        payload = body_for()
        first = self.post(payload, "customer-import-1")
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(first.json()["status"], "imported")
        self.assertEqual(CustomerServiceConversation.objects.count(), 1)

        replay = self.post(payload, "customer-import-1")
        self.assertEqual(replay.status_code, 201, replay.content)
        self.assertEqual(replay["X-Teruisi-Write-Replay"], "1")
        self.assertEqual(CustomerServiceConversation.objects.count(), 1)

        duplicate_payload = copy.deepcopy(payload)
        duplicate_payload["rawFileHash"] = hashlib.sha256(b"another-raw-file").hexdigest()
        duplicate = self.post(duplicate_payload, "customer-import-2")
        self.assertEqual(duplicate.status_code, 200, duplicate.content)
        self.assertEqual(duplicate.json()["status"], "duplicate")
        self.assertEqual(CustomerServiceImportCount(), 1)

        collision_payload = copy.deepcopy(payload)
        collision_payload["rawFileHash"] = hashlib.sha256(b"collision").hexdigest()
        collision = self.post(collision_payload, "customer-import-1")
        self.assertEqual(collision.status_code, 409, collision.content)
        self.assertEqual(collision.json()["code"], "version_conflict")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_query_uses_left_closed_right_open_dates_and_scope_denial(self) -> None:
        response = self.post(body_for(), "customer-import-query")
        self.assertEqual(response.status_code, 201, response.content)
        url = "/api/customer-service/conversations?startDate=2026-09-01&endDate=2026-09-01"
        listed = self.client.get(url, headers=signed_headers(url))
        self.assertEqual(listed.status_code, 200, listed.content)
        self.assertEqual(listed.json()["summary"]["total"], 1)
        self.assertTrue(listed["X-Customer-Service-Data-Revision"].startswith("2:"))
        ambiguous_history = self.client.get(
            "/api/customer-service/imports?pageSize=2&limit=3",
            headers=signed_headers("/api/customer-service/imports?pageSize=2&limit=3"),
        )
        self.assertEqual(ambiguous_history.status_code, 400)
        denied = self.client.get(
            "/api/customer-service/conversations",
            headers=signed_headers("/api/customer-service/conversations", scope={"warehouses": ["主仓"], "channels": [], "platforms": []}),
        )
        self.assertEqual(denied.status_code, 403)


def CustomerServiceImportCount() -> int:
    from customer_service.models import CustomerServiceImportBatch

    return CustomerServiceImportBatch.objects.count()

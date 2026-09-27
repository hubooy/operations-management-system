from __future__ import annotations

import base64
import hashlib
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.utils import timezone

from sales.models import (
    SalesDataRevision,
    SalesImportAttempt,
    SalesImportBatch,
    SalesImportFingerprint,
    SalesImportScopeHead,
    SalesOrderLine,
    SalesRawUploadChunk,
    SalesRawUploadSession,
    SalesStagedImportSession,
    SalesWriteAuthority,
)
from sales.policy import POLICY_PATH, sales_import_policy
from sales.tests.cutover_fixtures import install_writer_runtime_guard
from sales.write_service import (
    SalesImportServiceError,
    ScopeReservation,
    _claim_staged_session,
    _prepare_import,
    _publish_import,
    _reserve_scope,
    begin_raw_upload,
    begin_staged_import,
    claim_raw_upload,
    cleanup_raw_upload_chunks,
    complete_staged_import,
    finish_raw_upload,
    list_expired_raw_uploads,
    lock_active_write_authority,
    purge_expired_raw_upload,
    read_raw_upload_chunk,
    register_raw_upload_chunk,
    stage_normalized_chunk,
)


AUTHORITY_EPOCH = "11111111-1111-4111-8111-111111111111"
CUTOVER_ID = "sales-cutover-20260828"
CHANNEL_A = "京东-志高商用厨电旗舰店"
CHANNEL_B = "天猫-志高拓丰专卖店"


def raw_chunk_registration(
    upload_id: str,
    chunk_index: int,
    content: bytes,
    *,
    object_key: str | None = None,
) -> dict[str, object]:
    checksum = hashlib.sha256(content).hexdigest()
    return {
        "uploadId": upload_id,
        "chunkIndex": chunk_index,
        "sizeBytes": len(content),
        "sha256": checksum,
        "objectKey": object_key
        or (
            f"sales-upload/{upload_id}/{chunk_index:06d}-{checksum}-"
            f"{uuid.uuid4()}"
        ),
        "contentBase64": base64.b64encode(content).decode("ascii"),
    }


def normalized_row(number: int, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "sourceLineKey": f"line-{number}",
        "sourceRowHash": f"row-hash-{number}",
        "sourceRowNumber": number,
        "orderNo": f"order-{number}",
        "onlineOrderNo": "",
        "channel": CHANNEL_A,
        "platform": "京东",
        "shopName": "志高测试店",
        "logisticsCompany": "测试物流",
        "warehouse": "主仓",
        "productCode": f"P-{number}",
        "onlineSpecCode": f"SKU-{number}",
        "productName": "测试货品",
        "specification": "标准",
        "barcode": f"barcode-{number}",
        "supplier": "测试供应商",
        "category": "测试类目",
        "quantity": 1,
        "listUnitPriceCents": 5_000,
        "costAmountCents": 3_000,
        "allocatedUnitPriceCents": 5_000,
        "allocatedAmountCents": 5_000,
        "feeAllocationCents": 100,
        "grossProfitCents": 1_900,
        "grossMarginBps": 3_800,
        "untaxedGrossProfitCents": 1_900,
        "untaxedGrossMarginBps": 3_800,
        "orderTime": "2024-01-01 08:00:00",
        "salesTime": "2024-01-01 09:00:00",
        "shipTime": "2024-01-01 10:00:00",
        "lineShipTime": "2024-01-01 10:00:00",
        "businessType": "sale",
    }
    row.update(overrides)
    return row


@override_settings(
    DJANGO_PROCESS_ROLE="sales_writer",
    DJANGO_EXPECT_READ_ONLY=False,
    SALES_WRITE_AUTHORITY_EPOCH=AUTHORITY_EPOCH,
    SALES_WRITE_CUTOVER_ID=CUTOVER_ID,
)
class SalesWriteServiceTests(TestCase):
    def setUp(self) -> None:
        SalesWriteAuthority.objects.filter(id=1).update(
            status="active",
            authority_epoch=AUTHORITY_EPOCH,
            cutover_id=CUTOVER_ID,
            activated_at=timezone.now(),
        )
        install_writer_runtime_guard(CUTOVER_ID)
        self.erp_runtime_revision = tuple(
            SalesDataRevision.objects.filter(domain="erp")
            .values_list("revision", "source_digest")
            .get()
        )

    def test_writer_authority_fence_uses_shared_lock_and_plain_select(self) -> None:
        with patch(
            "sales.write_service.acquire_sales_write_authority_shared_lock"
        ) as acquire, patch.object(
            SalesWriteAuthority.objects,
            "select_for_update",
            side_effect=AssertionError("writer must not require authority UPDATE privilege"),
        ):
            authority = lock_active_write_authority()
        acquire.assert_called_once_with()
        self.assertEqual(authority.status, "active")
        self.assertEqual(str(authority.authority_epoch), AUTHORITY_EPOCH)

    def test_packaged_policy_matches_controlled_repository_policy(self) -> None:
        repository_policy = json.loads(
            (Path(__file__).resolve().parents[3] / "config" / "sales-import-policy.json")
            .read_text(encoding="utf-8")
        )
        packaged_policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(packaged_policy, repository_policy)
        self.assertEqual(sales_import_policy()["timeZone"], "Asia/Shanghai")

    def _begin_stage(
        self,
        rows: list[dict[str, object]],
        *,
        fingerprint: str,
        raw_hash: str,
        start_date: str = "2024-01-01",
        end_date: str = "2024-01-02",
        expected_channels: list[str] | None = None,
        system_cost_snapshot: dict[str, object] | None = None,
    ) -> str:
        payload: dict[str, object] = {
            "fingerprint": fingerprint,
            "fileName": f"{fingerprint}.xlsx",
            "fileSizeBytes": 1_024,
            "rawFileHash": raw_hash,
            "sheetName": "销售单明细账",
            "expectedStartDate": start_date,
            "expectedEndDate": end_date,
            "expectedChannels": expected_channels,
            "chunkCount": 1,
            "sourceTotals": {},
            "parserWarnings": [],
        }
        if system_cost_snapshot is not None:
            payload["systemCostSnapshot"] = system_cost_snapshot
        session = begin_staged_import(payload, "admin@example.test")
        stage_normalized_chunk(
            {
                "sessionId": session["id"],
                "chunkIndex": 0,
                "rows": rows,
            },
            "admin@example.test",
        )
        return str(session["id"])

    def test_atomic_replace_content_idempotency_and_channel_scope(self) -> None:
        first_rows = [
            normalized_row(1),
            normalized_row(
                2,
                channel=CHANNEL_B,
                platform="天猫",
                shipTime="2024-01-02 10:00:00",
                salesTime="2024-01-02 09:00:00",
            ),
        ]
        first_session = self._begin_stage(
            first_rows, fingerprint="first", raw_hash="a" * 64
        )
        first = complete_staged_import(first_session, "admin@example.test")
        self.assertEqual(first["status"], "imported")
        first_batch = str(first["batch"]["id"])
        self.assertEqual(SalesOrderLine.objects.count(), 2)
        self.assertEqual(SalesDataRevision.objects.get(domain="sales").revision, 1)

        reordered = [
            {
                **first_rows[1],
                "sourceLineKey": "technical-key-b",
                "sourceRowHash": "technical-hash-b",
                "sourceRowNumber": 200,
            },
            {
                **first_rows[0],
                "sourceLineKey": "technical-key-a",
                "sourceRowHash": "technical-hash-a",
                "sourceRowNumber": 100,
            },
        ]
        duplicate_session = self._begin_stage(
            reordered, fingerprint="reordered", raw_hash="b" * 64
        )
        duplicate = complete_staged_import(duplicate_session, "admin@example.test")
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(duplicate["batch"]["id"], first_batch)
        self.assertEqual(SalesImportBatch.objects.count(), 1)
        self.assertEqual(SalesDataRevision.objects.get(domain="sales").revision, 1)

        replacement = normalized_row(
            9,
            sourceLineKey="replacement-a",
            allocatedAmountCents=6_500,
            grossProfitCents=3_400,
        )
        replacement_session = self._begin_stage(
            [replacement],
            fingerprint="replacement",
            raw_hash="c" * 64,
            expected_channels=[CHANNEL_A],
        )
        changed = complete_staged_import(replacement_session, "admin@example.test")
        self.assertEqual(changed["status"], "imported")
        self.assertNotEqual(changed["batch"]["id"], first_batch)
        self.assertEqual(SalesDataRevision.objects.get(domain="sales").revision, 2)
        self.assertEqual(SalesImportBatch.objects.count(), 2)
        self.assertEqual(SalesOrderLine.objects.count(), 2)
        self.assertEqual(
            set(SalesOrderLine.objects.values_list("channel", flat=True)),
            {CHANNEL_A, CHANNEL_B},
        )
        self.assertFalse(SalesOrderLine.objects.filter(source_line_key="line-1").exists())
        self.assertTrue(SalesOrderLine.objects.filter(source_line_key="replacement-a").exists())
        self.assertEqual(SalesImportFingerprint.objects.count(), 2)
        self.assertEqual(
            SalesImportAttempt.objects.filter(outcome="duplicate").count(), 1
        )

    def test_rolling_45_day_refresh_updates_orders_without_accumulation_and_preserves_outside_scope(self) -> None:
        def row(number: int, day: str, **changes: object) -> dict[str, object]:
            return normalized_row(number, orderTime=f"{day} 08:00:00", salesTime=f"{day} 09:00:00",
                                  shipTime=f"{day} 10:00:00", lineShipTime=f"{day} 10:00:00", **changes)

        def publish(rows: list[dict[str, object]], label: str, start: str, end: str,
                    channels: list[str] | None = None) -> dict[str, object]:
            session = self._begin_stage(rows, fingerprint=label, raw_hash=hashlib.sha256(label.encode()).hexdigest(),
                                        start_date=start, end_date=end, expected_channels=channels)
            return complete_staged_import(session, "admin@example.test")

        first = [row(1, "2024-08-08"), row(2, "2024-08-09"), row(3, "2024-09-01"),
                 row(4, "2024-09-22"), row(5, "2024-09-23"),
                 row(6, "2024-09-15", channel=CHANNEL_B, platform="天猫")]
        publish(first, "rolling-baseline", "2024-08-08", "2024-09-23")
        latest = [row(2, "2024-08-09", quantity=3, allocatedAmountCents=15_000,
                      costAmountCents=9_000, grossProfitCents=5_900), first[3], row(7, "2024-08-31")]
        changed = publish(latest, "rolling-latest", "2024-08-09", "2024-09-22", [CHANNEL_A])
        self.assertEqual(changed["status"], "imported")
        updated = SalesOrderLine.objects.get(order_no="order-2")
        self.assertEqual(updated.quantity, 3)
        self.assertEqual(updated.allocated_amount_cents, 15_000)
        self.assertEqual(SalesOrderLine.objects.filter(order_no="order-2").count(), 1)
        self.assertFalse(SalesOrderLine.objects.filter(order_no="order-3").exists())
        self.assertEqual(SalesOrderLine.objects.count(), 6)
        for number in (1, 5, 6):
            self.assertEqual(SalesOrderLine.objects.get(order_no=f"order-{number}").allocated_amount_cents, 5_000)
        revision = SalesDataRevision.objects.get(domain="sales").revision
        duplicate = publish(latest, "rolling-same-new-file", "2024-08-09", "2024-09-22", [CHANNEL_A])
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(SalesDataRevision.objects.get(domain="sales").revision, revision)
        self.assertEqual(SalesOrderLine.objects.count(), 6)

        # The leftmost day leaves the next window and must remain as history.
        shifted = [first[3], first[4], row(7, "2024-08-31", allocatedAmountCents=6_000, grossProfitCents=2_900)]
        publish(shifted, "rolling-next-day", "2024-08-10", "2024-09-23", [CHANNEL_A])
        self.assertEqual(SalesOrderLine.objects.get(order_no="order-2").quantity, 3)
        self.assertEqual(SalesOrderLine.objects.get(order_no="order-7").allocated_amount_cents, 6_000)
        self.assertEqual(SalesOrderLine.objects.count(), 6)

        # A late failure after row updates and removals cannot publish a partial refresh.
        fields = ("source_line_key", "quantity", "allocated_amount_cents", "last_import_batch_id")
        facts = list(SalesOrderLine.objects.order_by("source_line_key").values_list(*fields))
        revision = SalesDataRevision.objects.get(domain="sales").revision
        with patch("sales.write_service._bump_sales_revision", side_effect=RuntimeError("fixture publication failure")):
            with self.assertRaises(RuntimeError):
                publish([row(7, "2024-08-31", allocatedAmountCents=7_000, grossProfitCents=3_900)],
                        "rolling-rollback", "2024-08-10", "2024-09-23", [CHANNEL_A])
        self.assertEqual(list(SalesOrderLine.objects.order_by("source_line_key").values_list(*fields)), facts)
        self.assertEqual(SalesDataRevision.objects.get(domain="sales").revision, revision)

    def test_rolling_scope_shift_with_unchanged_rows_publishes_exact_new_scope_receipt(self) -> None:
        rows = [normalized_row(31, orderTime="2024-09-01 08:00:00", salesTime="2024-09-01 09:00:00",
                               shipTime="2024-09-01 10:00:00", lineShipTime="2024-09-01 10:00:00")]
        first = complete_staged_import(self._begin_stage(rows, fingerprint="rolling-edge-first", raw_hash="a" * 64,
                                                         start_date="2024-08-09", end_date="2024-09-22"), "admin@example.test")
        shifted = complete_staged_import(self._begin_stage(rows, fingerprint="rolling-edge-shifted", raw_hash="b" * 64,
                                                           start_date="2024-08-10", end_date="2024-09-23"), "admin@example.test")
        self.assertEqual(shifted["status"], "imported")
        self.assertNotEqual(first["batch"]["id"], shifted["batch"]["id"])
        batch = SalesImportBatch.objects.get(id=shifted["batch"]["id"])
        self.assertEqual(batch.scope_json["startDate"], "2024-08-10")
        self.assertEqual(batch.scope_json["endDate"], "2024-09-23")
        self.assertEqual(batch.raw_file_hash, "b" * 64)
        self.assertEqual(SalesOrderLine.objects.count(), 1)
        self.assertEqual(SalesOrderLine.objects.get().last_import_batch_id, batch.id)
        duplicate = complete_staged_import(self._begin_stage(rows, fingerprint="rolling-edge-repeat", raw_hash="c" * 64,
                                                             start_date="2024-08-10", end_date="2024-09-23"), "admin@example.test")
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(duplicate["batch"]["id"], batch.id)

    def test_first_post_cutover_upload_proves_composite_legacy_current_facts_duplicate(self) -> None:
        rows = [
            normalized_row(1),
            normalized_row(
                2,
                channel=CHANNEL_B,
                platform="天猫",
                shipTime="2024-01-02 10:00:00",
                salesTime="2024-01-02 09:00:00",
            ),
        ]
        session_id = self._begin_stage(
            rows, fingerprint="legacy-seed", raw_hash="8" * 64
        )
        imported = complete_staged_import(session_id, "admin@example.test")
        original_batch = SalesImportBatch.objects.get(id=imported["batch"]["id"])
        SalesImportBatch.objects.create(
            id="legacy-current-part-2",
            source="legacy",
            file_name="legacy.xlsx",
            file_size_bytes=1,
            file_hash="9" * 64,
            sheet_name="销售单明细账",
            status="completed",
            row_count=1,
            inserted_count=1,
            duplicate_count=0,
            warning_count=0,
            warnings_json="[]",
            totals_json="{}",
            created_at=timezone.now().isoformat(),
            completed_at=timezone.now().isoformat(),
        )
        SalesOrderLine.objects.filter(source_line_key="line-2").update(
            last_import_batch_id="legacy-current-part-2"
        )
        SalesImportBatch.objects.filter(id=original_batch.id).update(
            raw_file_hash="", content_hash="", scope_key="", scope_json={}
        )
        SalesImportFingerprint.objects.all().delete()
        revision_before = SalesDataRevision.objects.get(domain="sales").revision

        replay_session = self._begin_stage(
            list(reversed(rows)),
            fingerprint="legacy-exact-replay",
            raw_hash="a" * 64,
        )
        replay = complete_staged_import(replay_session, "admin@example.test")
        self.assertEqual(replay["status"], "duplicate")
        self.assertEqual(
            SalesDataRevision.objects.get(domain="sales").revision,
            revision_before,
        )
        self.assertEqual(SalesOrderLine.objects.count(), 2)
        self.assertEqual(
            set(SalesOrderLine.objects.values_list("last_import_batch_id", flat=True)),
            {original_batch.id, "legacy-current-part-2"},
        )
        self.assertEqual(SalesImportFingerprint.objects.count(), 1)

    def test_shanghai_cutoff_brush_exclusion_and_system_cost_cleaning(self) -> None:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        rows = [
            normalized_row(
                1,
                quantity=2,
                costAmountCents=0,
                grossProfitCents=4_900,
                productCode="P-COST",
            ),
            normalized_row(2, warehouse=" 刷刷仓 "),
            normalized_row(
                3,
                shipTime=f"{today} 10:00:00",
                salesTime=f"{today} 09:00:00",
            ),
        ]
        session_id = self._begin_stage(
            rows,
            fingerprint="cost-clean",
            raw_hash="d" * 64,
            start_date="2024-01-01",
            end_date="2024-01-01",
            system_cost_snapshot={
                "sourceBatchId": "inventory-batch-1",
                "snapshotDate": "2024-01-01",
                "costs": [
                    {
                        "productCode": "P-COST",
                        "warehouse": "主仓",
                        "unitCostCents": 1_200,
                    }
                ],
            },
        )
        result = complete_staged_import(session_id, "admin@example.test")
        self.assertEqual(result["status"], "imported")
        fact = SalesOrderLine.objects.get()
        self.assertEqual(fact.cost_amount_cents, 2_400)
        self.assertEqual(fact.gross_profit_cents, 2_500)
        warning_codes = {item.get("code") for item in result["warnings"]}
        self.assertIn("SYSTEM_COST_CLEANED", warning_codes)
        self.assertIn("EXCLUDED_BRUSH_WAREHOUSE", warning_codes)
        self.assertIn("EXCLUDED_FUTURE_DATE_ROWS", warning_codes)

    def test_prevalidation_rejection_is_audit_only(self) -> None:
        with self.assertRaisesRegex(SalesImportServiceError, "未创建"):
            begin_staged_import(
                {
                    "fingerprint": "parser-rejected",
                    "fileName": "rejected.xlsx",
                    "fileSizeBytes": 100,
                    "rawFileHash": "e" * 64,
                    "sheetName": "销售单明细账",
                    "expectedStartDate": "2024-01-01",
                    "expectedEndDate": "2024-01-01",
                    "chunkCount": 1,
                    "sourceTotals": {},
                    "parserWarnings": [
                        {"code": "PARSER_WARNING", "message": "非阻断告警"}
                    ],
                    "parserErrors": [
                        {
                            "code": "INVALID_XLSX_SIGNATURE",
                            "message": "文件签名无效",
                        }
                    ],
                    "systemCostSnapshot": None,
                },
                "admin@example.test",
            )
        attempt = SalesImportAttempt.objects.get(outcome="rejected")
        self.assertEqual(attempt.error_code, "INVALID_XLSX_SIGNATURE")
        self.assertEqual(SalesStagedImportSession.objects.count(), 0)
        self.assertEqual(SalesImportScopeHead.objects.count(), 0)
        self.assertEqual(SalesImportFingerprint.objects.count(), 0)
        self.assertEqual(SalesOrderLine.objects.count(), 0)

    def test_raw_upload_claim_rotates_owner_and_fences_late_finish(self) -> None:
        file_size = 2 * 1024 * 1024 + 7
        upload = begin_raw_upload(
            {
                "fingerprint": "raw-upload-one",
                "fileName": "sales.xlsx",
                "fileSizeBytes": file_size,
                "chunkCount": 2,
                "expectedStartDate": "2024-01-01",
                "expectedEndDate": "2024-01-02",
            },
            "admin@example.test",
        )
        upload_id = str(upload["id"])
        contents = [b"a" * (2 * 1024 * 1024), b"second!"]
        registrations = [
            raw_chunk_registration(upload_id, index, content)
            for index, content in enumerate(contents)
        ]
        for index in range(2):
            object_key = str(registrations[index]["objectKey"])
            registered = register_raw_upload_chunk(
                registrations[index],
                "admin@example.test",
            )
            if index == 0:
                first_object_key = object_key
        self.assertEqual(registered["status"], "ready")
        retry = raw_chunk_registration(upload_id, 0, contents[0])
        retry_object_key = str(retry["objectKey"])
        retried = register_raw_upload_chunk(
            retry,
            "admin@example.test",
        )
        self.assertEqual(retried["discardedObjectKey"], retry_object_key)
        self.assertEqual(
            SalesRawUploadChunk.objects.get(
                session_id=upload_id, chunk_index=0
            ).object_key,
            first_object_key,
        )
        with self.assertRaisesRegex(SalesImportServiceError, "存储键"):
            invalid_key = raw_chunk_registration(upload_id, 1, contents[1])
            invalid_key["objectKey"] = f"{invalid_key['objectKey']}/unexpected"
            register_raw_upload_chunk(
                invalid_key,
                "admin@example.test",
            )

        first_claim = claim_raw_upload(upload_id, "admin@example.test")
        first_owner = str(first_claim["ownerToken"])
        stored = read_raw_upload_chunk(
            {
                "uploadId": upload_id,
                "chunkIndex": 1,
                "ownerToken": first_owner,
            },
            "admin@example.test",
        )
        self.assertEqual(base64.b64decode(str(stored["contentBase64"])), contents[1])
        SalesRawUploadSession.objects.filter(id=upload_id).update(
            updated_at=timezone.now() - timedelta(minutes=31)
        )
        second_claim = claim_raw_upload(upload_id, "admin@example.test")
        second_owner = str(second_claim["ownerToken"])
        self.assertNotEqual(first_owner, second_owner)
        with self.assertRaisesRegex(SalesImportServiceError, "当前处理 owner"):
            read_raw_upload_chunk(
                {
                    "uploadId": upload_id,
                    "chunkIndex": 1,
                    "ownerToken": first_owner,
                },
                "admin@example.test",
            )
        with self.assertRaisesRegex(SalesImportServiceError, "新 owner"):
            finish_raw_upload(
                upload_id,
                "admin@example.test",
                owner_token=first_owner,
                completed=True,
            )
        reset = finish_raw_upload(
            upload_id,
            "admin@example.test",
            owner_token=second_owner,
            completed=False,
        )
        self.assertEqual(reset["status"], "ready")
        final_claim = claim_raw_upload(upload_id, "admin@example.test")
        self.assertGreater(final_claim["ownerGeneration"], first_claim["ownerGeneration"])

    def test_staged_chunks_and_completion_follow_raw_owner_fence(self) -> None:
        upload = begin_raw_upload(
            {
                "fingerprint": "raw-linked-stage",
                "fileName": "linked.xlsx",
                "fileSizeBytes": 1,
                "chunkCount": 1,
                "expectedStartDate": "2024-01-01",
                "expectedEndDate": "2024-01-01",
            },
            "admin@example.test",
        )
        upload_id = str(upload["id"])
        register_raw_upload_chunk(
            raw_chunk_registration(upload_id, 0, b"3"),
            "admin@example.test",
        )
        first_owner = str(
            claim_raw_upload(upload_id, "admin@example.test")["ownerToken"]
        )
        init_payload: dict[str, object] = {
            "rawUploadId": upload_id,
            "rawUploadOwnerToken": first_owner,
            "fingerprint": "linked-normalized",
            "fileName": "linked.xlsx",
            "fileSizeBytes": 1,
            "rawFileHash": "4" * 64,
            "sheetName": "销售单明细账",
            "expectedStartDate": "2024-01-01",
            "expectedEndDate": "2024-01-01",
            "chunkCount": 1,
            "sourceTotals": {},
        }
        staged = begin_staged_import(init_payload, "admin@example.test")
        with self.assertRaisesRegex(SalesImportServiceError, "其他 owner"):
            stage_normalized_chunk(
                {
                    "sessionId": staged["id"],
                    "chunkIndex": 0,
                    "rows": [normalized_row(1)],
                    "rawUploadOwnerToken": "f" * 32,
                },
                "admin@example.test",
            )
        stage_normalized_chunk(
            {
                "sessionId": staged["id"],
                "chunkIndex": 0,
                "rows": [normalized_row(1)],
                "rawUploadOwnerToken": first_owner,
            },
            "admin@example.test",
        )
        staged_state = SalesStagedImportSession.objects.get(id=staged["id"])
        self.assertEqual(staged_state.status, "ready")
        self.assertEqual(staged_state.received_chunk_count, 1)

        stale_at = timezone.now() - timedelta(minutes=31)
        SalesRawUploadSession.objects.filter(id=upload_id).update(updated_at=stale_at)
        second_owner = str(
            claim_raw_upload(upload_id, "admin@example.test")["ownerToken"]
        )
        self.assertNotEqual(first_owner, second_owner)
        with self.assertRaisesRegex(SalesImportServiceError, "其他 owner"):
            complete_staged_import(
                staged["id"], "admin@example.test", first_owner
            )

        resumed_payload = {**init_payload, "rawUploadOwnerToken": second_owner}
        resumed = begin_staged_import(resumed_payload, "admin@example.test")
        self.assertEqual(resumed["id"], staged["id"])
        self.assertEqual(resumed["status"], "ready")
        result = complete_staged_import(
            resumed["id"], "admin@example.test", second_owner
        )
        self.assertEqual(result["status"], "imported")
        self.assertEqual(SalesOrderLine.objects.count(), 1)
        completed = finish_raw_upload(
            upload_id,
            "admin@example.test",
            owner_token=second_owner,
            completed=True,
            result_batch_id=result["batch"]["id"],
        )
        self.assertEqual(completed["status"], "completed")
        replay = claim_raw_upload(upload_id, "admin@example.test")
        self.assertEqual(replay["result"]["batch"]["id"], result["batch"]["id"])
        cleanup = cleanup_raw_upload_chunks(upload_id, "admin@example.test")
        self.assertEqual(len(cleanup["removedObjectKeys"]), 1)
        self.assertEqual(cleanup_raw_upload_chunks(upload_id, "admin@example.test")["removedObjectKeys"], [])

    def test_expired_raw_cleanup_requires_exact_generation(self) -> None:
        uploader = "uploader@example.test"
        upload = begin_raw_upload(
            {
                "fingerprint": "expired-cleanup",
                "fileName": "expired.xlsx",
                "fileSizeBytes": 1,
                "chunkCount": 1,
                "expectedStartDate": "2024-01-01",
                "expectedEndDate": "2024-01-01",
            },
            uploader,
        )
        upload_id = str(upload["id"])
        registration = raw_chunk_registration(upload_id, 0, b"d")
        register_raw_upload_chunk(
            registration,
            uploader,
        )
        old_claim = claim_raw_upload(upload_id, uploader)
        SalesRawUploadSession.objects.filter(id=upload_id).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        candidate = list_expired_raw_uploads("admin@example.test")["items"][0]
        self.assertEqual(candidate["ownerGeneration"], 2)
        replay_candidate = list_expired_raw_uploads("admin@example.test")["items"][0]
        self.assertEqual(replay_candidate["cleanupToken"], candidate["cleanupToken"])
        self.assertEqual(replay_candidate["ownerGeneration"], candidate["ownerGeneration"])
        SalesRawUploadSession.objects.filter(id=upload_id).update(
            expires_at=timezone.now() + timedelta(hours=1)
        )
        with self.assertRaises(SalesImportServiceError):
            claim_raw_upload(upload_id, uploader)
        with self.assertRaises(SalesImportServiceError):
            finish_raw_upload(
                upload_id,
                uploader,
                owner_token=old_claim["ownerToken"],
                completed=False,
            )
        with self.assertRaises(SalesImportServiceError):
            register_raw_upload_chunk(
                registration,
                uploader,
            )
        with self.assertRaisesRegex(SalesImportServiceError, "栅栏"):
            purge_expired_raw_upload(
                upload_id,
                "admin@example.test",
                owner_generation=3,
                cleanup_token=candidate["cleanupToken"],
            )
        purge_expired_raw_upload(
            upload_id,
            "admin@example.test",
            owner_generation=2,
            cleanup_token=candidate["cleanupToken"],
        )
        expired = SalesRawUploadSession.objects.get(id=upload_id)
        self.assertEqual(expired.status, "expired")
        self.assertEqual(expired.received_chunk_count, 0)
        self.assertEqual(SalesRawUploadChunk.objects.filter(session=expired).count(), 0)
        self.assertLessEqual(expired.expires_at, timezone.now())

        # PostgreSQL deletion is terminal; expired tombstones are not leased
        # again because there is no external object-store orphan to recheck.
        SalesRawUploadSession.objects.filter(id=upload_id).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(
            list_expired_raw_uploads("another-admin@example.test")["items"], []
        )

    def test_scope_stale_takeover_fences_old_owner(self) -> None:
        old_session_id = self._begin_stage(
            [normalized_row(1)], fingerprint="old-owner", raw_hash="6" * 64
        )
        old_session, old_owner = _claim_staged_session(
            old_session_id, "admin@example.test"
        )
        self.assertIsNotNone(old_owner)
        old_prepared = _prepare_import(old_session)  # type: ignore[arg-type]
        old_reservation = _reserve_scope(old_prepared, str(old_owner))
        self.assertIsInstance(old_reservation, ScopeReservation)

        new_session_id = self._begin_stage(
            [normalized_row(2, allocatedAmountCents=6_000)],
            fingerprint="new-owner",
            raw_hash="7" * 64,
        )
        new_session, new_owner = _claim_staged_session(
            new_session_id, "admin@example.test"
        )
        new_prepared = _prepare_import(new_session)  # type: ignore[arg-type]
        SalesImportScopeHead.objects.update(
            updated_at=timezone.now() - timedelta(minutes=31)
        )
        new_reservation = _reserve_scope(new_prepared, str(new_owner))
        self.assertIsInstance(new_reservation, ScopeReservation)
        self.assertNotEqual(
            old_reservation.attempt_id,  # type: ignore[union-attr]
            new_reservation.attempt_id,  # type: ignore[union-attr]
        )
        old_attempt = SalesImportAttempt.objects.get(
            id=old_reservation.attempt_id  # type: ignore[union-attr]
        )
        self.assertEqual(old_attempt.outcome, "failed")
        self.assertEqual(old_attempt.error_code, "IMPORT_RESERVATION_EXPIRED")

        with self.assertRaisesRegex(SalesImportServiceError, "接管"):
            _publish_import(
                old_prepared,
                old_reservation,  # type: ignore[arg-type]
                str(old_owner),
            )
        batch = _publish_import(
            new_prepared,
            new_reservation,  # type: ignore[arg-type]
            str(new_owner),
        )
        self.assertEqual(SalesOrderLine.objects.get().last_import_batch_id, batch.id)

    def test_publication_failure_rolls_back_all_business_state(self) -> None:
        session_id = self._begin_stage(
            [normalized_row(1)], fingerprint="rollback", raw_hash="8" * 64
        )
        with patch(
            "sales.write_service._upsert_rows", side_effect=RuntimeError("injected")
        ):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                complete_staged_import(session_id, "admin@example.test")
        self.assertEqual(SalesOrderLine.objects.count(), 0)
        self.assertEqual(SalesImportBatch.objects.count(), 0)
        self.assertEqual(SalesImportFingerprint.objects.count(), 0)
        self.assertFalse(SalesDataRevision.objects.filter(domain="sales").exists())
        self.assertEqual(
            tuple(SalesDataRevision.objects.filter(domain="erp").values_list("revision", "source_digest").get()),
            self.erp_runtime_revision,
        )
        head = SalesImportScopeHead.objects.get()
        self.assertEqual(head.status, "ready")
        self.assertEqual(head.state_token, "initial")
        self.assertEqual(head.owner_token, "")
        self.assertEqual(SalesImportAttempt.objects.get().outcome, "failed")
        session = SalesStagedImportSession.objects.get(id=session_id)
        self.assertEqual(session.status, "ready")
        self.assertEqual(session.owner_token, "")

    def test_fact_publication_rechecks_authority_in_same_transaction(self) -> None:
        session_id = self._begin_stage(
            [normalized_row(1)], fingerprint="authority-fence", raw_hash="9" * 64
        )
        session, owner = _claim_staged_session(session_id, "admin@example.test")
        prepared = _prepare_import(session)  # type: ignore[arg-type]
        reservation = _reserve_scope(prepared, str(owner))
        SalesWriteAuthority.objects.filter(id=1).update(status="disabled")
        with self.assertRaisesRegex(SalesImportServiceError, "尚未取得"):
            _publish_import(
                prepared,
                reservation,  # type: ignore[arg-type]
                str(owner),
            )
        self.assertEqual(SalesOrderLine.objects.count(), 0)
        self.assertEqual(SalesImportBatch.objects.count(), 0)
        self.assertFalse(SalesDataRevision.objects.filter(domain="sales").exists())
        self.assertEqual(
            tuple(SalesDataRevision.objects.filter(domain="erp").values_list("revision", "source_digest").get()),
            self.erp_runtime_revision,
        )

    def test_inactive_authority_rejects_even_staging_metadata(self) -> None:
        SalesWriteAuthority.objects.filter(id=1).update(status="pending")
        with self.assertRaisesRegex(SalesImportServiceError, "尚未取得"):
            begin_raw_upload(
                {
                    "fingerprint": "inactive",
                    "fileName": "sales.xlsx",
                    "fileSizeBytes": 1,
                    "chunkCount": 1,
                    "expectedStartDate": "2024-01-01",
                    "expectedEndDate": "2024-01-01",
                },
                "admin@example.test",
            )

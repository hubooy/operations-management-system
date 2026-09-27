from __future__ import annotations

import copy
import hashlib
import uuid
from datetime import date, timedelta
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from market.annotations import candidate_counts
from market.models import (
    MarketAnnotationItem,
    MarketAnnotationJob,
    MarketDataRevision,
    MarketImportAttempt,
    MarketImportBatch,
    MarketImportScopeHead,
    MarketMasterIdentity,
    MarketPriceBandItem,
    MarketPriceBandVersion,
    MarketPriceSnapshot,
    MarketRankingEntry,
    MarketSkuAnnotation,
    MarketWriteAuthority,
)
from sales.tests.factories import TEST_SECRET, signed_headers

from .factories import body_bytes, market_row, prepared_payload


AUTHORITY_EPOCH = "11111111-1111-4111-8111-111111111111"
CUTOVER_ID = "market-test-cutover"


@override_settings(
    MARKET_WRITE_AUTHORITY_EPOCH=AUTHORITY_EPOCH,
    MARKET_WRITE_CUTOVER_ID=CUTOVER_ID,
)
class MarketApiContractTests(TestCase):
    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_master_pagination_preserves_auth_revision_and_read_only_contract(self):
        for view in ("database_primary", "pending_prices"):
            payload = {"operation": "master", "view": view, "params": {"page": 100, "pageSize": 30}}
            response = self.post_query(payload, "master-paging-"+view, role="viewer")
            self.assertEqual(response.status_code, 200, response.content)
            result = response.json()["masterData"] if view == "database_primary" else response.json()
            self.assertEqual(result["pagination"], {"page":1,"pageSize":30,"total":0,"pageCount":1})
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertIn("X-Market-Data-Revision", response)
            rejected = self.post_query(payload, "master-scoped-"+view, scope={"warehouses":[],"channels":[],"platforms":["京东"]})
            self.assertEqual(rejected.status_code, 403)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_system_kpis_keep_signed_read_scope_and_response_contract(self):
        payload = {"operation": "master", "view": "system_kpis", "params": {}}
        response = self.post_query(payload, "kpi-read", role="viewer")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(set(response.json()), {"marketIdentityTotal", "pendingPriceCount", "pendingAiCount",
            "completedAiCount", "sameImageReuseCount", "priceOnlyRecognitionCount", "fullRecognitionCount",
            "blockedRecognitionCount"})
        self.assertEqual(response["Cache-Control"], "no-store")
        rejected = self.post_query(payload, "kpi-scoped", scope={"warehouses":[],"channels":[],"platforms":["京东"]})
        self.assertEqual(rejected.status_code, 403)
        unsigned = self.client.post("/api/market/queries", data=body_bytes(payload), content_type="application/json")
        self.assertEqual(unsigned.status_code, 401)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_independent_filters_keep_query_auth_and_strict_shape(self):
        response = self.post_query({"operation": "filter_options"}, "filters-read", role="analyst")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("categories", response.json()["filters"])
        rejected = self.post_query({"operation": "filter_options", "arbitrary": "value"}, "filters-invalid")
        self.assertEqual(rejected.status_code, 400)
        scoped = self.post_query({"operation": "filter_options"}, "filters-scoped",
            scope={"warehouses": [], "channels": [], "platforms": ["京东"]})
        self.assertEqual(scoped.status_code, 403)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_annotation_progress_is_read_only_and_derives_live_counts(self) -> None:
        job = MarketAnnotationJob.objects.create(
            id="progress-job", category="净水", prompt_version_id="prompt",
            executor="cloud", status="queued", total_count=999,
            created_by="admin@example.test",
        )
        original = MarketAnnotationJob.objects.filter(id=job.id).values().get()
        statuses = ["queued", "inferencing", "failed", "failed", "approved", "committed", "superseded"]
        for index, status in enumerate(statuses):
            MarketAnnotationItem.objects.create(
                id=f"progress-item-{index}", job_id=job.id, category=job.category,
                sku_code=f"sku-{index}", status=status,
                attempt_count=3 if index == 3 else 1,
                lease_expires_at=timezone.now() + timedelta(minutes=1),
            )

        def reject_writes(execute, sql, params, many, context):
            self.assertNotIn(sql.lstrip().split()[0].upper(), {"INSERT", "UPDATE", "DELETE"})
            return execute(sql, params, many, context)

        for poll in range(2):
            with connection.execute_wrapper(reject_writes):
                response = self.post_query(
                    {"operation": "annotations", "view": "progress", "params": {"jobId": job.id}},
                    f"progress-read-{poll}", role="viewer",
                )
            self.assertEqual(response.status_code, 200, response.content)
            result = response.json()
            self.assertEqual(result["job"]["totalCount"], 6)
            self.assertEqual(result["job"]["completedCount"], 2)
            self.assertEqual(result["job"]["failedCount"], 2)
            self.assertEqual(result["remainingInferenceUnits"], 3)
            self.assertEqual(result["activeClaims"], 1)
        self.assertEqual(MarketAnnotationJob.objects.filter(id=job.id).values().get(), original)

        MarketAnnotationItem.objects.filter(job_id=job.id).exclude(status="superseded").update(status="committed")
        with connection.execute_wrapper(reject_writes):
            settled = self.post_query(
                {"operation": "annotations", "view": "progress", "params": {"jobId": job.id}},
                "progress-read-settled", role="viewer",
            )
        self.assertEqual(settled.status_code, 200, settled.content)
        self.assertEqual(settled.json()["job"]["status"], "committed")
        self.assertEqual(settled.json()["remainingInferenceUnits"], 0)
        self.assertEqual(MarketAnnotationJob.objects.filter(id=job.id).values().get(), original)

    def setUp(self) -> None:
        MarketWriteAuthority.objects.filter(id=1).update(
            status="postgres",
            authority_epoch=uuid.UUID(AUTHORITY_EPOCH),
            cutover_id=CUTOVER_ID,
            migration_verify_run_id="market-test-migration",
            activated_at=timezone.now(),
        )

    def post_import(self, payload: dict[str, object], request_id: str):
        body = body_bytes(payload)
        return self.client.post(
            "/api/market/imports",
            data=body,
            content_type="application/json; charset=utf-8",
            headers=signed_headers(
                "/api/market/imports",
                method="POST",
                body=body,
                request_id=request_id,
            ),
        )

    def post_query(
        self,
        payload: dict[str, object],
        request_id: str,
        *,
        role: str = "admin",
        scope=None,
    ):
        body = body_bytes(payload)
        return self.client.post(
            "/api/market/queries",
            data=body,
            content_type="application/json; charset=utf-8",
            headers=signed_headers(
                "/api/market/queries",
                method="POST",
                body=body,
                request_id=request_id,
                role=role,
                scope=scope,
            ),
        )

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_daily_coverage_preserves_exact_daily_price_band_contract(self) -> None:
        common = {
            "category": "商用净水设备",
            "scope": "pop",
            "ranking_dimension": "SKU",
            "last_import_batch_id": "daily-coverage-batch",
        }
        for index, sku_code in enumerate(("SKU-1", "SKU-2"), start=1):
            MarketRankingEntry.objects.create(
                natural_key=f"daily-all-{index}",
                source_row_number=index,
                period_start="2026-08-01",
                period_end="2026-08-01",
                price_band_filter="全部",
                sku_code=sku_code,
                **common,
            )
        MarketRankingEntry.objects.create(
            natural_key="daily-other-price-band",
            source_row_number=3,
            period_start="2026-08-02",
            period_end="2026-08-02",
            price_band_filter="1000-2000",
            sku_code="SKU-3",
            **common,
        )
        MarketRankingEntry.objects.create(
            natural_key="monthly-must-not-cover-daily-gaps",
            source_row_number=4,
            period_start="2026-08-01",
            period_end="2026-08-03",
            price_band_filter="全部",
            sku_code="SKU-4",
            **common,
        )

        response = self.post_query(
            {
                "operation": "daily_coverage",
                "category": "商用净水设备",
                "scope": "pop",
                "rankingDimension": "SKU",
                "priceBandFilter": "全部",
                "startDate": "2026-08-01",
                "endDate": "2026-08-03",
            },
            "market-daily-coverage-exact-contract",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "identity": {
                    "category": "商用净水设备",
                    "scope": "pop",
                    "rankingDimension": "SKU",
                    "priceBandFilter": "全部",
                },
                "startDate": "2026-08-01",
                "endDate": "2026-08-03",
                "cutoffDate": "2026-08-01",
                "presentDates": ["2026-08-01"],
                "missingDates": ["2026-08-02", "2026-08-03"],
                "rowCounts": {"2026-08-01": 2},
            },
        )

        other_band = self.post_query(
            {
                "operation": "daily_coverage",
                "category": "商用净水设备",
                "scope": "pop",
                "rankingDimension": "SKU",
                "priceBandFilter": "1000-2000",
                "startDate": "2026-08-01",
                "endDate": "2026-08-03",
            },
            "market-daily-coverage-other-band",
        )
        self.assertEqual(other_band.status_code, 200, other_band.content)
        self.assertEqual(other_band.json()["presentDates"], ["2026-08-02"])
        self.assertEqual(other_band.json()["missingDates"], ["2026-08-01", "2026-08-03"])

    def test_candidate_counts_is_set_based_and_keeps_exact_image_identity(self) -> None:
        rows = []
        for index, (category, sku_code) in enumerate(
            (("净水", "SKU-A"), ("净水", "SKU-B"), ("制冰", "SKU-C")),
            start=1,
        ):
            row = MarketRankingEntry.objects.create(
                natural_key=f"candidate-count-{index}",
                source_row_number=index,
                period_start="2026-08-01",
                period_end="2026-08-31",
                category=category,
                scope="热销商品榜",
                ranking_dimension="SKU",
                sku_code=sku_code,
                last_import_batch_id="candidate-count-batch",
            )
            MarketMasterIdentity.objects.create(
                category=category,
                scope="热销商品榜",
                ranking_dimension="SKU",
                sku_code=sku_code,
                latest_entry_id=row.id,
            )
            rows.append(row)
        for row, image_hash in zip(rows[:2], ("a" * 64, "b" * 64), strict=True):
            MarketPriceSnapshot.objects.create(
                id=f"snapshot-{row.sku_code}",
                category=row.category,
                scope=row.scope,
                sku_code=row.sku_code,
                ranking_dimension=row.ranking_dimension,
                month="2026-08",
                image_content_sha256=image_hash,
            )
        MarketSkuAnnotation.objects.create(
            id="annotation-sku-a",
            category="净水",
            scope="热销商品榜",
            ranking_dimension="SKU",
            sku_code="SKU-A",
            image_content_sha256="a" * 64,
            segment="商用直饮机",
            source_job_item_id="annotation-item-a",
            prompt_version_id="prompt-a",
            reviewed_by="admin@example.test",
            reviewed_at=timezone.now(),
        )
        with self.assertNumQueries(2):
            result = candidate_counts()
        self.assertEqual(
            result,
            {
                "categories": [
                    {"value": "净水", "candidateCount": 1},
                    {"value": "制冰", "candidateCount": 0},
                ]
            },
        )

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_import_is_atomic_business_idempotent_and_replay_fenced(self) -> None:
        payload = prepared_payload(market_row())
        first = self.post_import(payload, "market-import-1")
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(first.json()["status"], "imported")
        self.assertEqual(first["X-Market-Data-Revision"].split(":")[0], "1")
        self.assertEqual(MarketRankingEntry.objects.get().natural_key, payload["rows"][0]["naturalKey"])
        self.assertEqual(MarketMasterIdentity.objects.count(), 1)

        replay = self.post_import(payload, "market-import-1")
        self.assertEqual(replay.status_code, 201, replay.content)
        self.assertEqual(replay["X-Teruisi-Write-Replay"], "1")
        self.assertEqual(MarketImportBatch.objects.count(), 1)

        resaved = copy.deepcopy(payload)
        resaved["rawFileHash"] = hashlib.sha256(b"resaved").hexdigest()
        resaved["fileName"] = "重新保存.xlsx"
        duplicate = self.post_import(resaved, "market-import-2")
        self.assertEqual(duplicate.status_code, 200, duplicate.content)
        self.assertEqual(duplicate.json()["status"], "duplicate")
        self.assertEqual(MarketImportAttempt.objects.filter(outcome="duplicate").count(), 1)

        collision = copy.deepcopy(payload)
        collision["rawFileHash"] = hashlib.sha256(b"collision").hexdigest()
        collision_response = self.post_import(collision, "market-import-1")
        self.assertEqual(collision_response.status_code, 409)
        self.assertEqual(collision_response.json()["code"], "version_conflict")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_changed_exact_scope_replaces_complete_fact_set_and_prunes_derived_rows(self) -> None:
        first = prepared_payload(
            market_row(),
            market_row(sourceRowNumber=3, skuCode="SKU-REMOVED", rank=2, productName="将被删除"),
        )
        self.assertEqual(self.post_import(first, "market-replace-1").status_code, 201)
        self.assertEqual(MarketRankingEntry.objects.count(), 2)

        replacement = prepared_payload(
            market_row(gmvCents=8_800_000, quantity=80),
            raw_seed="replacement",
        )
        response = self.post_import(replacement, "market-replace-2")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(MarketRankingEntry.objects.count(), 1)
        self.assertEqual(MarketRankingEntry.objects.get().gmv_cents, 8_800_000)
        self.assertFalse(MarketMasterIdentity.objects.filter(sku_code="SKU-REMOVED").exists())
        self.assertFalse(MarketPriceSnapshot.objects.filter(sku_code="SKU-REMOVED").exists())
        head = MarketImportScopeHead.objects.get()
        self.assertEqual(head.status, "ready")
        self.assertEqual(head.owner_token, "")
        self.assertEqual(MarketDataRevision.objects.get(domain="market").revision, 2)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_exact_scope_duplicate_survives_another_date_in_the_same_lock_month(self) -> None:
        first = prepared_payload(
            market_row(periodStart="2026-08-01", periodEnd="2026-08-01"),
            raw_seed="day-one",
        )
        second = prepared_payload(
            market_row(
                sourceRowNumber=3,
                periodStart="2026-08-02",
                periodEnd="2026-08-02",
                gmvCents=6_000_000,
            ),
            raw_seed="day-two",
        )
        self.assertEqual(self.post_import(first, "market-day-one").status_code, 201)
        self.assertEqual(self.post_import(second, "market-day-two").status_code, 201)
        retry = copy.deepcopy(first)
        retry["fileName"] = "第一天重新保存.xlsx"
        retry["rawFileHash"] = hashlib.sha256(b"day-one-resaved").hexdigest()
        response = self.post_import(retry, "market-day-one-retry")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "duplicate")
        self.assertEqual(MarketRankingEntry.objects.count(), 2)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_prevalidation_failure_is_audited_without_claiming_scope(self) -> None:
        payload = prepared_payload(market_row())
        payload["rows"][0]["naturalKey"] = "tampered"
        response = self.post_import(payload, "market-rejected-1")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(MarketImportBatch.objects.count(), 0)
        self.assertEqual(MarketImportScopeHead.objects.count(), 0)
        attempt = MarketImportAttempt.objects.get()
        self.assertEqual(attempt.outcome, "rejected")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_writer_fails_closed_without_exact_authority(self) -> None:
        MarketWriteAuthority.objects.filter(id=1).update(status="d1", authority_epoch=None)
        response = self.post_import(prepared_payload(market_row()), "market-authority-off")
        self.assertEqual(response.status_code, 503, response.content)
        self.assertEqual(response.json()["code"], "service_unavailable")
        self.assertEqual(MarketRankingEntry.objects.count(), 0)

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    @patch("market.query.read_sales_consumer")
    def test_overview_is_revisioned_and_keeps_formal_price_separate(self, sales_reader) -> None:
        self.assertEqual(self.post_import(prepared_payload(market_row()), "market-overview-import").status_code, 201)
        snapshot = MarketPriceSnapshot.objects.get()
        snapshot.confirmed_market_price_cents = 188_800
        snapshot.ai_price_type = "标准售价"
        snapshot.confirmation_status = "confirmed"
        snapshot.image_content_sha256 = "a" * 64
        snapshot.save()
        sales_reader.return_value = (
            {"rows": [{"productCode": "SKU-001", "owned": True, "ownSalesCents": 99_900}]},
            "7:abcdefabcdef",
        )
        response = self.post_query(
            {
                "operation": "overview",
                "view": "full",
                "page": 1,
                "pageSize": 20,
                "filters": None,
            },
            "market-overview-1",
            role="analyst",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertRegex(response["X-Market-Data-Revision"], r"^1:[a-f0-9]{12}$")
        item = response.json()["items"][0]
        self.assertEqual(item["marketPriceCents"], 188_800)
        self.assertEqual(item["averageTransactionPriceCents"], 188_800)
        self.assertEqual(item["ownSalesCents"], 99_900)
        self.assertTrue(item["isOwn"])
        self.assertEqual(response.json()["industryReport"]["definition"]["metricScope"], "当前 TOP 榜单覆盖市场")

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_ranking_overview_reads_sales_only_for_current_page(self) -> None:
        MarketRankingEntry.objects.bulk_create(
            [
                MarketRankingEntry(
                    natural_key=f"large-overview-{index}",
                    source_row_number=index + 1,
                    period_start="2026-08-01",
                    period_end="2026-08-31",
                    category="商用净饮水设备",
                    scope="热销商品榜",
                    ranking_dimension="SKU",
                    rank=index + 1,
                    sku_code=f"SKU-{index:04d}",
                    product_name=f"商品 {index}",
                    gmv_cents=100_000,
                    quantity=1,
                    last_import_batch_id="large-overview-batch",
                )
                for index in range(1_001)
            ]
        )
        requests: list[dict[str, object]] = []

        def sales_reader(_principal, payload):
            requests.append(payload)
            product_codes = payload["productCodes"]
            self.assertLessEqual(len(product_codes), 1_000)
            return (
                {
                    "rows": [
                        {
                            "productCode": product_code,
                            "owned": False,
                            "ownSalesCents": 0,
                        }
                        for product_code in product_codes
                    ]
                },
                "11:7",
            )

        with patch("market.query.read_sales_consumer", side_effect=sales_reader):
            response = self.post_query(
                {
                    "operation": "overview",
                    "view": "ranking",
                    "page": 1,
                    "pageSize": 20,
                    "filters": {
                        "rankingDimensions": ["SKU"],
                        "startDate": "2026-08-01",
                        "endDate": "2026-08-31",
                    },
                },
                "market-overview-large-product-set",
                role="analyst",
            )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["pagination"]["total"], 1_001)
        self.assertEqual(sorted(len(item["productCodes"]) for item in requests), [20])

    def create_industry_fixture(self, *, missing_month: str = "", invalid_price_month: str = "") -> None:
        MarketPriceBandVersion.objects.create(
            id="market-price-band-v1",
            category="商用净饮水设备",
            version=1,
            status="published",
            effective_from="2025-01-01",
            created_by="admin@example.test",
            published_by="admin@example.test",
            published_at=timezone.now(),
        )
        MarketPriceBandItem.objects.create(
            id="market-price-band-v1-item",
            version_id="market-price-band-v1",
            label="1000-3000",
            min_cents=100_000,
            max_cents=300_000,
            sort_order=1,
        )
        for offset in range(13):
            absolute = 2025 * 12 + 7 + offset
            year = absolute // 12
            month = absolute % 12 + 1
            period = f"{year:04d}-{month:02d}"
            if period == missing_month:
                continue
            next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
            period_end = (next_month - timedelta(days=1)).isoformat()
            MarketRankingEntry.objects.create(
                natural_key=f"industry|{period}",
                source_row_number=offset + 1,
                period_start=f"{period}-01",
                period_end=period_end,
                category="商用净饮水设备",
                scope="热销商品榜",
                price_band_filter="全部",
                ranking_dimension="SKU",
                operation_mode="POP",
                subcategory="校园饮水机",
                rank=1,
                sku_code="SKU-INDUSTRY",
                product_name="校园RO反渗透商用直饮机 100人 包安装",
                brand="志高",
                price_cents=188_800,
                gmv_cents=1_000_000 + offset * 100_000,
                quantity=10 + offset,
                visitors=100 + offset * 10,
                last_import_batch_id="industry-fixture",
            )
            MarketPriceSnapshot.objects.create(
                id=f"price|{period}",
                category="商用净饮水设备",
                scope="热销商品榜",
                sku_code="SKU-INDUSTRY",
                ranking_dimension="SKU",
                month=period,
                confirmed_market_price_cents=188_800,
                ai_price_type="起售价" if period == invalid_price_month else "标准售价",
                image_content_sha256="b" * 64,
                confirmation_status="confirmed",
                confirmed_by="admin@example.test",
                confirmed_at=timezone.now(),
            )

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    @patch("market.query.read_sales_consumer")
    def test_industry_report_requires_continuous_coverage_and_image_fenced_formal_prices(self, sales_reader) -> None:
        self.create_industry_fixture()
        sales_reader.return_value = (
            {"rows": [{"productCode": "SKU-INDUSTRY", "owned": False, "ownSalesCents": 0}]},
            "8:abcdefabcdef",
        )
        response = self.post_query(
            {"operation": "overview", "view": "full", "page": 1, "pageSize": 20, "filters": None},
            "market-industry-ready",
            role="analyst",
        )
        self.assertEqual(response.status_code, 200, response.content)
        report = response.json()["industryReport"]
        self.assertTrue(report["dataQuality"]["identityReady"])
        self.assertTrue(report["dataQuality"]["coverageReady"])
        self.assertTrue(report["dataQuality"]["comparisonReady"])
        self.assertEqual(report["dataQuality"]["pendingPriceSkuCount"], 0)
        self.assertEqual(report["period"]["coverageMonths"], 13)
        self.assertTrue(report["lifecycle"])
        self.assertTrue(report["brandConcentrationTrend"])
        self.assertTrue(report["trafficQuadrants"])
        self.assertGreater(report["productSignals"]["sampleSize"], 0)
        self.assertTrue(report["opportunities"][0]["decisionReady"])

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    @patch("market.query.read_sales_consumer")
    def test_industry_report_rejects_gap_and_non_formal_starting_price(self, sales_reader) -> None:
        self.create_industry_fixture(missing_month="2026-02", invalid_price_month="2026-08")
        sales_reader.return_value = (
            {"rows": [{"productCode": "SKU-INDUSTRY", "owned": False, "ownSalesCents": 0}]},
            "9:abcdefabcdef",
        )
        response = self.post_query(
            {"operation": "overview", "view": "full", "page": 1, "pageSize": 20, "filters": None},
            "market-industry-not-ready",
            role="analyst",
        )
        self.assertEqual(response.status_code, 200, response.content)
        result = response.json()
        report = result["industryReport"]
        self.assertFalse(report["dataQuality"]["coverageReady"])
        self.assertEqual(report["dataQuality"]["pendingPriceSkuCount"], 1)
        self.assertIsNone(next(item for item in result["items"] if item["periodEnd"].startswith("2026-08"))["marketPriceCents"])
        self.assertTrue(all(item["recommendation"] == "持续观察" for item in report["opportunities"]))
        self.assertTrue(all(not item["decisionReady"] for item in report["opportunities"]))

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_market_reader_rejects_scoped_principal_and_duplicate_json_keys(self) -> None:
        scoped = self.post_query(
            {"operation": "master", "view": "settings_status", "params": {}},
            "market-scoped",
            role="analyst",
            scope={"warehouses": [], "channels": [], "platforms": ["京东"]},
        )
        self.assertEqual(scoped.status_code, 403)
        body = b'{"operation":"image_metadata","contentHash":"' + b"a" * 64 + b'","contentHash":"' + b"b" * 64 + b'"}'
        duplicate = self.client.post(
            "/api/market/queries",
            data=body,
            content_type="application/json",
            headers=signed_headers(
                "/api/market/queries",
                method="POST",
                body=body,
                request_id="market-duplicate-json",
            ),
        )
        self.assertEqual(duplicate.status_code, 400)

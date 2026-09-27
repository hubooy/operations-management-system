from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from market.annotations import (
    _claim_job_item, _complete_job_item, _dispatch, _dispatch_lease,
    _refresh_job, _set_cloud_run, _set_concurrency,
)
from market.errors import MarketApiError
from market.models import (
    MarketAnnotationCloudRun, MarketAnnotationConcurrencySetting,
    MarketAnnotationItem, MarketAnnotationJob, MarketAnnotationPromptVersion,
)
from sales.auth import Principal


PRINCIPAL = Principal("annotation-test@example.test", "Annotation test", "operator", None)
RESULT = {"segment": "测试细分", "imagePriceCents": 10000, "priceType": "到手价", "confidenceBps": 9000, "reason": "可见价格"}


class QueueFixtures:
    def job(self, identifier, *, category=None, model="vision-test", concurrency=10):
        category = category or identifier
        prompt, _ = MarketAnnotationPromptVersion.objects.get_or_create(
            id=f"prompt-{category}", defaults={"category": category, "version": 1, "source": "manual",
                "status": "active", "segments_json": ["测试细分"], "prompt_body": "严格 JSON", "created_by": PRINCIPAL.email},
        )
        job = MarketAnnotationJob.objects.create(id=identifier, category=category, prompt_version_id=prompt.id,
            executor="cloud", model_id=model, created_by=PRINCIPAL.email, total_count=1)
        MarketAnnotationCloudRun.objects.create(job_id=identifier, state="running")
        MarketAnnotationConcurrencySetting.objects.update_or_create(category=category, executor="cloud",
            defaults={"concurrency": concurrency, "updated_by": PRINCIPAL.email})
        return job

    def item(self, job, identifier, *, sku=None, month="2026-09", scope="全部", image_hash="a" * 64, **kwargs):
        return MarketAnnotationItem.objects.create(id=identifier, job_id=job.id, category=job.category,
            scope=scope, sku_code=sku or identifier, month=month, image_content_sha256=image_hash, **kwargs)

    def claim(self, job=None, **extra):
        return _claim_job_item({"jobId": job.id if job else "", **extra}, PRINCIPAL)

    def complete(self, task, **extra):
        return _complete_job_item({"itemId": task["itemId"], "leaseToken": task["leaseToken"], "result": RESULT, **extra}, PRINCIPAL)


class AnnotationQueueTests(QueueFixtures, TestCase):
    def test_dispatch_and_claim_without_expired_leases_skip_backlog_quarantine_update(self):
        job = self.job("no-expired-leases", concurrency=2)
        self.item(job, "one")
        self.item(job, "two")
        with CaptureQueriesContext(connection) as queries:
            lease = _dispatch_lease({"jobId": job.id}, release=False)
            first = self.claim(job, coordinatorToken=lease["coordinatorToken"])["task"]
            second = self.claim(job, coordinatorToken=lease["coordinatorToken"])["task"]
        self.assertEqual({first["itemId"], second["itemId"]}, {"one", "two"})
        quarantine_updates = [query["sql"] for query in queries.captured_queries
                              if query["sql"].startswith('UPDATE "market_annotation_items"')
                              and "EXISTS" in query["sql"]]
        self.assertEqual(quarantine_updates, [])
        self.assertEqual(MarketAnnotationCloudRun.objects.get(job_id=job.id).state, "running")

    def test_full_oldest_job_does_not_starve_another_plan(self):
        first = self.job("old", concurrency=1)
        second = self.job("new", concurrency=1)
        self.item(first, "old-item")
        self.item(second, "new-item")
        self.assertEqual(self.claim(first)["task"]["jobId"], first.id)
        self.assertEqual(self.claim()["task"]["jobId"], second.id)

    def test_finished_stale_job_does_not_hide_runnable_job(self):
        first = self.job("old")
        self.item(first, "capped", status="failed", attempt_count=3)
        second = self.job("new")
        MarketAnnotationCloudRun.objects.filter(job_id=second.id).update(last_started_at=timezone.now())
        self.item(second, "fresh")
        self.assertEqual(self.claim()["task"]["jobId"], second.id)
        first.refresh_from_db()
        self.assertEqual(first.status, "review_ready")

    def test_default_cloud_concurrency_is_ten_and_new_limit_applies_to_leases(self):
        job = self.job("defaults")
        MarketAnnotationConcurrencySetting.objects.all().delete()
        for index in range(12):
            self.item(job, f"item-{index}")
        first = self.claim(job)
        self.assertEqual(first["workerConcurrency"], 10)
        self.assertEqual(first["task"]["ownerEmail"], PRINCIPAL.email)
        expiry = MarketAnnotationItem.objects.get(id=first["task"]["itemId"]).lease_expires_at
        self.assertLessEqual((expiry - timezone.now()).total_seconds(), 180)
        _set_concurrency({"category": job.category, "executor": "cloud", "concurrency": 1}, PRINCIPAL)
        self.assertIsNone(self.claim(job)["task"])
        _set_concurrency({"category": job.category, "executor": "cloud", "concurrency": 3}, PRINCIPAL)
        self.assertIsNotNone(self.claim(job)["task"])

    def test_coordinator_fencing_pause_resume_and_stale_release(self):
        job = self.job("coordinator")
        self.item(job, "one")
        self.item(job, "two")
        lease = _dispatch_lease({"jobId": job.id}, release=False)
        self.assertIsNone(_dispatch_lease({"jobId": job.id}, release=False)["coordinatorToken"])
        self.assertIsNone(self.claim(job)["task"])
        task = self.claim(job, coordinatorToken=lease["coordinatorToken"])["task"]
        self.assertIsNotNone(task)
        _set_cloud_run({"jobId": job.id, "state": "paused"}, PRINCIPAL)
        _set_cloud_run({"jobId": job.id, "state": "running"}, PRINCIPAL)
        new_lease = _dispatch_lease({"jobId": job.id}, release=False)
        self.assertFalse(_dispatch_lease({"jobId": job.id, "coordinatorToken": lease["coordinatorToken"]}, release=True)["released"])
        self.assertIsNone(self.claim(job, coordinatorToken=lease["coordinatorToken"])["task"])
        self.assertIsNotNone(self.claim(job, coordinatorToken=new_lease["coordinatorToken"])["task"])

    def test_cross_month_same_image_claims_once_and_diffuses_atomically(self):
        job = self.job("dedup")
        first = self.item(job, "one", sku="same", month="2026-08")
        second = self.item(job, "two", sku="same", month="2026-09")
        different_scope = self.item(job, "three", sku="same", scope="另一榜单")
        task = self.claim(job)["task"]
        another = self.claim(job)["task"]
        self.assertEqual(another["itemId"], different_scope.id)
        self.assertIsNone(self.claim(job)["task"])
        # A simulated downstream write failure preserves the original lease for completion replay.
        with patch("market.annotations._refresh_job", side_effect=RuntimeError("synthetic persistence failure")):
            with self.assertRaises(RuntimeError):
                self.complete(task)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, "claimed")
        self.assertEqual(second.status, "queued")
        completed = self.complete(task)
        self.assertEqual(completed["reusedCount"], 1)
        second.refresh_from_db()
        self.assertEqual(second.status, "review_pending")
        self.assertEqual(second.ai_image_price_cents, 10000)
        self.assertTrue(self.complete(task)["duplicate"])

    def test_unknown_expired_lease_quarantines_all_months_and_pauses(self):
        job = self.job("unknown")
        self.item(job, "one", sku="same", month="2026-08")
        follower = self.item(job, "two", sku="same")
        self.item(job, "three", sku="different")
        task = self.claim(job)["task"]
        MarketAnnotationItem.objects.filter(id=task["itemId"]).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(self.claim(job)["task"])
        run = MarketAnnotationCloudRun.objects.get(job_id=job.id)
        self.assertEqual(run.state, "paused")
        self.assertEqual(run.last_failure_code, "inference_result_unknown")
        follower.refresh_from_db()
        self.assertEqual((follower.status, follower.attempt_count), ("failed", 3))
        with self.assertRaises(MarketApiError):
            self.complete(task)
        _set_cloud_run({"jobId": job.id, "state": "running"}, PRINCIPAL)
        self.assertEqual(self.claim(job)["task"]["itemId"], "three")

    def test_account_failure_pauses_before_consuming_more_items_and_keeps_retry_budget(self):
        job = self.job("account")
        self.item(job, "one")
        self.item(job, "two")
        task = self.claim(job)["task"]
        self.complete(task, error="secret provider text", failureCode="authorization_revoked")
        run = MarketAnnotationCloudRun.objects.get(job_id=job.id)
        self.assertEqual(run.state, "paused")
        self.assertNotIn("secret", run.last_failure_message)
        self.assertEqual(MarketAnnotationItem.objects.get(id=task["itemId"]).attempt_count, 0)
        self.assertIsNone(self.claim(job)["task"])

    def test_last_unknown_group_remains_paused_even_after_progress_refresh(self):
        job = self.job("only-unknown")
        self.item(job, "one", sku="same", month="2026-08", attempt_count=2)
        self.item(job, "two", sku="same", attempt_count=2)
        task = self.claim(job)["task"]
        MarketAnnotationItem.objects.filter(id=task["itemId"]).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual([row["jobId"] for row in _dispatch({})["jobs"]], [job.id])
        self.assertTrue(_dispatch_lease({"jobId": job.id}, release=False)["paused"])
        _refresh_job(job.id)
        run = MarketAnnotationCloudRun.objects.get(job_id=job.id)
        self.assertEqual(run.state, "paused")
        self.assertEqual(run.last_failure_code, "inference_result_unknown")
        with self.assertRaises(MarketApiError):
            _set_cloud_run({"jobId": job.id, "state": "running"}, PRINCIPAL)

    def test_retry_budget_is_shared_across_months_and_bad_image_does_not_pause_plan(self):
        job = self.job("budget")
        self.item(job, "one", sku="same", month="2026-08")
        self.item(job, "two", sku="same")
        for _ in range(3):
            task = self.claim(job)["task"]
            self.complete(task, error="invalid image", failureCode="image_fetch")
        self.assertIsNone(self.claim(job)["task"])
        self.assertEqual(set(MarketAnnotationItem.objects.filter(job_id=job.id).values_list("attempt_count", flat=True)), {3})
        self.assertEqual(MarketAnnotationCloudRun.objects.get(job_id=job.id).state, "completed")
        with self.assertRaises(MarketApiError):
            _set_cloud_run({"jobId": job.id, "state": "running"}, PRINCIPAL)

    def test_late_final_failure_cannot_erase_another_items_unknown_quarantine(self):
        for failure_code in ("image_fetch", "model_timeout"):
            with self.subTest(failure_code=failure_code):
                job = self.job(f"late-{failure_code}", concurrency=2)
                self.item(job, f"unknown-{failure_code}")
                self.item(job, f"final-failure-{failure_code}", attempt_count=2)
                claimed = [self.claim(job)["task"], self.claim(job)["task"]]
                tasks = {task["itemId"]: task for task in claimed}
                unknown = tasks[f"unknown-{failure_code}"]
                late = tasks[f"final-failure-{failure_code}"]
                MarketAnnotationItem.objects.filter(id=unknown["itemId"]).update(
                    lease_expires_at=timezone.now() - timedelta(seconds=1),
                )
                self.assertIsNone(self.claim(job)["task"])
                run = MarketAnnotationCloudRun.objects.get(job_id=job.id)
                self.assertEqual(run.last_failure_code, "inference_result_unknown")
                reason = run.last_failure_message
                self.complete(late, error="late model failure", failureCode=failure_code)
                late_item = MarketAnnotationItem.objects.get(id=late["itemId"])
                self.assertEqual((late_item.status, late_item.attempt_count), ("failed", 3))
                self.assertNotEqual(late_item.error_message, reason)
                run.refresh_from_db()
                self.assertEqual((run.state, run.last_failure_code, run.last_failure_message),
                                 ("paused", "inference_result_unknown", reason))

    def test_transient_lane_cooldown_does_not_block_other_lanes_or_plans(self):
        job = self.job("transient", concurrency=4)
        for index in range(6):
            self.item(job, f"item-{index}")
        task = self.claim(job)["task"]
        self.complete(task, error="timeout", failureCode="model_timeout")
        dispatch = _dispatch({})["jobs"]
        self.assertEqual(dispatch[0]["effectiveConcurrency"], 3)
        self.assertEqual(dispatch[0]["availableSlots"], 2)
        self.assertIsNotNone(self.claim(job)["task"])

    def test_rate_limit_blocks_same_model_only_and_dispatch_obeys_cooldown(self):
        first = self.job("rate-one", model="shared")
        second = self.job("rate-two", model="shared")
        other = self.job("other", model="other-model")
        for job in (first, second, other):
            self.item(job, f"{job.id}-item")
        self.complete(self.claim(first)["task"], error="429", failureCode="provider_rate_limit", retryAfterMs=60_000)
        self.assertIsNone(self.claim(second)["task"])
        self.assertEqual([job["jobId"] for job in _dispatch({})["jobs"]], [other.id])
        self.assertIsNotNone(self.claim(other)["task"])

    def test_cooled_first_five_plans_do_not_hide_a_ready_sixth_plan(self):
        for index in range(5):
            job = self.job(f"cooled-{index}", model="cooled-model")
            self.item(job, f"cooled-item-{index}")
        MarketAnnotationCloudRun.objects.filter(job_id="cooled-0").update(
            retry_state_json={"globalRateLimitUntil": int(timezone.now().timestamp() * 1000) + 60_000},
        )
        ready = self.job("ready-sixth", model="ready-model")
        self.item(ready, "ready-item")
        self.assertEqual([row["jobId"] for row in _dispatch({"limit": 5})["jobs"]], [ready.id])


@skipUnless(connection.vendor == "postgresql", "Requires real PostgreSQL row and advisory locks")
class AnnotationQueueConcurrencyTests(QueueFixtures, TransactionTestCase):
    def parallel_claims(self, job, count=4):
        barrier = Barrier(count)
        def claim_one(_):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return self.claim(job)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=count) as pool:
            return list(pool.map(claim_one, range(count)))

    def test_simultaneous_claims_never_exceed_database_limit_or_duplicate_month_identity(self):
        job = self.job("parallel", concurrency=1)
        self.item(job, "one", sku="same", month="2026-08")
        self.item(job, "two", sku="same")
        self.item(job, "three", sku="different")
        results = self.parallel_claims(job)
        self.assertEqual(sum(result["task"] is not None for result in results), 1)
        self.assertEqual(MarketAnnotationItem.objects.filter(job_id=job.id, status="claimed").count(), 1)

    def test_simultaneous_explicit_lanes_fill_configured_capacity(self):
        job = self.job("fill-capacity", concurrency=4)
        for index in range(8):
            self.item(job, f"item-{index}")
        results = self.parallel_claims(job)
        claimed = [result["task"]["itemId"] for result in results if result["task"]]
        self.assertEqual(len(set(claimed)), 4)
        self.assertEqual(MarketAnnotationItem.objects.filter(job_id=job.id, status="claimed").count(), 4)

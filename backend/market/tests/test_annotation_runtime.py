from django.test import SimpleTestCase

from market.annotation_runtime import blocked_until, record_failure, record_success, retry_snapshot


class AnnotationRuntimeTests(SimpleTestCase):
    def test_transient_failures_only_cool_their_channels_and_count_one_incident(self):
        retry = retry_snapshot({}, 10)
        self.assertFalse(record_failure(retry, "model_timeout", 0, 100_000))
        self.assertEqual(retry["currentConcurrency"], 8)
        self.assertEqual(blocked_until(retry, 0), 105_000)
        self.assertEqual(blocked_until(retry, 1), 0)
        record_failure(retry, "model_network", 1, 100_010)
        self.assertEqual(retry["transientFailureCount"], 1)
        self.assertEqual(retry["currentConcurrency"], 8)
        record_failure(retry, "model_timeout", 0, 110_000)
        self.assertEqual(retry["currentConcurrency"], 4)
        self.assertEqual(blocked_until(retry, 0), 120_000)

    def test_rate_limit_is_global_bounded_and_does_not_extend_on_concurrent_failure(self):
        retry = retry_snapshot({}, 10)
        record_failure(retry, "provider_rate_limit", 3, 100_000, 999_999)
        self.assertEqual(retry["currentConcurrency"], 5)
        self.assertEqual(blocked_until(retry, 0), 400_000)
        record_failure(retry, "provider_rate_limit", 0, 200_000)
        self.assertEqual(retry["currentConcurrency"], 5)
        self.assertEqual(blocked_until(retry, 4), 400_000)

    def test_three_independent_floor_failures_pause_and_success_resets_floor(self):
        retry = retry_snapshot({}, 1)
        self.assertFalse(record_failure(retry, "model_network", 0, 100_000))
        self.assertFalse(record_failure(retry, "model_network", 0, 106_000))
        self.assertTrue(record_failure(retry, "model_network", 0, 117_000))
        record_success(retry)
        self.assertEqual(retry["floorFailureCount"], 0)

    def test_three_successes_restore_one_lane_and_configuration_changes_apply(self):
        retry = retry_snapshot({}, 10)
        record_failure(retry, "model_timeout", 0, 100_000)
        for _ in range(3):
            record_success(retry)
        self.assertEqual(retry["currentConcurrency"], 9)
        self.assertEqual(retry_snapshot(retry, 20)["currentConcurrency"], 9)
        self.assertEqual(retry_snapshot(retry, 3)["currentConcurrency"], 3)
        self.assertEqual(retry_snapshot(retry_snapshot({}, 10), 20)["currentConcurrency"], 20)

    def test_only_account_and_configuration_permanent_failures_pause_whole_run(self):
        for code in ("authorization_revoked", "model_configuration"):
            self.assertTrue(record_failure(retry_snapshot({}, 10), code, 0, 100_000))
        for code in ("model_response", "image_fetch", "annotation_failed"):
            self.assertFalse(record_failure(retry_snapshot({}, 10), code, 0, 100_000))

"""Signed evidence detail exposes only bounded exact promotion selectors."""
from copy import deepcopy
from unittest.mock import patch

from django import test as djtest
from django.test.utils import CaptureQueriesContext
from django.db import connection
from sales.tests.factories import signed_headers, TEST_SECRET
from business_analysis.evidence_v2 import normalize_sources

from . import business_evidence as evidence, models as m
from . import test_business_promotion_creation as fixtures
from .policy import canonical


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionWorkbenchDetailTests(djtest.TransactionTestCase):
    user = fixtures.PromotionCreationTests.user
    call = fixtures.PromotionCreationTests.call
    collect_body = fixtures.PromotionCreationTests.collect_body
    bundle = fixtures.PromotionCreationTests.bundle
    input_for = fixtures.PromotionCreationTests.input_for
    insert = fixtures.PromotionCreationTests.insert
    seed = fixtures.PromotionCreationTests.seed
    setUp = fixtures.PromotionCreationTests.setUp
    request_body = fixtures.PromotionCreationTests.request_body

    def signed(self, run_id, actor=None, *, role="ai_reader", enabled=True):
        actor = actor or self.admin
        url = f"/api/ai/business-evidence/{run_id}"
        headers = signed_headers(url, email=actor.email, role=actor.role, scope=actor.scope)
        with (patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}),
              djtest.override_settings(DJANGO_INTERNAL_SECRET=TEST_SECRET,
                  DJANGO_PROCESS_ROLE=role, AI_PROMOTION_AGENT_RUNTIME_ENABLED=enabled),
              patch("ai_assistant.views.authority"),
              patch("ai_assistant.provider.turn", side_effect=AssertionError("no paid model"))):
            return self.client.get(url, headers=headers)

    def test_signed_sealed_detail_exact_choice_and_disabled_flag(self):
        before = tuple(model.objects.count() for model in (m.AiReportRun, m.AiBusinessEvidenceRun))
        with CaptureQueriesContext(connection) as queries:
            response = self.signed(self.parent.id)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Cache-Control"], "no-store")
        detail = response.json()["item"]
        self.assertTrue(detail["promotionSupported"])
        self.assertEqual(detail["promotionChoices"], [{"sourceKey":"ads", "platform":"京东",
            "shop":self.query["shop"], "startDate":self.query["startDate"],
            "endDate":self.query["endDate"], "baselineChoices":[]}])
        self.assertEqual(tuple(model.objects.count() for model in (m.AiReportRun, m.AiBusinessEvidenceRun)), before)
        self.assertFalse(any(query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for query in queries))
        disabled = self.signed(self.parent.id, enabled=False)
        self.assertEqual(disabled.status_code, 200, disabled.content)
        self.assertFalse(disabled.json()["item"]["promotionSupported"])
        self.assertEqual(disabled.json()["item"]["promotionChoices"], [])

    def test_exact_baseline_pairing_and_wrong_identity_never_joins(self):
        source = next(item for item in self.sources if item["key"] == "ads")
        previous = deepcopy(source)
        previous["key"] = "ads-previous"
        previous["query"]["window"] = "previous"
        year_ago = deepcopy(source)
        year_ago["key"] = "ads-year-ago"
        year_ago["query"]["window"] = "yearAgo"
        wrong_shop = deepcopy(previous)
        wrong_shop["key"] = "ads-other-shop"
        wrong_shop["query"]["shop"] = "其他店"
        wrong_date = deepcopy(previous)
        wrong_date["key"] = "ads-other-date"
        wrong_date["query"]["startDate"] = "2026-07-31"
        normalized = normalize_sources([*self.sources, previous, year_ago, wrong_shop])
        # A different original interval cannot enter a real sealed catalog;
        # inject it only into this read-only matching helper as an extra denial.
        choices = evidence._promotion_choices([*normalized, wrong_date])
        self.assertEqual(choices, [{"sourceKey":"ads", "platform":"京东", "shop":source["query"]["shop"],
            "startDate":source["query"]["startDate"], "endDate":source["query"]["endDate"],
            "baselineChoices":[{"sourceKey":"ads-previous", "window":"previous"},
                {"sourceKey":"ads-year-ago", "window":"yearAgo"}]}])

    def test_unsealed_legacy_and_other_actor_fail_closed(self):
        request = deepcopy(self.evidence_body)
        request["clientRequestId"] = "promotion-ui-unsealed"
        request["sources"] = deepcopy(self.sources)
        run = evidence.create(request, self.admin)["item"]
        unsealed = self.signed(run["id"])
        self.assertEqual(unsealed.status_code, 200, unsealed.content)
        self.assertFalse(unsealed.json()["item"]["promotionSupported"])
        self.assertEqual(unsealed.json()["item"]["promotionChoices"], [])
        legacy_id = evidence.create({**self.body, "clientRequestId":"promotion-ui-v1"}, self.admin)["item"]["id"]
        legacy = self.signed(legacy_id)
        self.assertEqual(legacy.status_code, 200, legacy.content)
        self.assertNotIn("promotionSupported", legacy.json()["item"])
        outsider = self.user("promotion-ui-outsider@example.invalid", "admin", None)
        denied = self.signed(self.parent.id, actor=outsider)
        self.assertIn(denied.status_code, (403, 404))

    def test_reader_enabled_writer_disabled_still_refuses_public_creation(self):
        detail = self.signed(self.parent.id, enabled=True)
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertTrue(detail.json()["item"]["promotionSupported"])
        body = {**self.request_body(), "analysisMode":"screening-promotion-v1", "dryRun":False}
        url = "/api/ai/business-reports"
        raw = canonical(body).encode("utf-8")
        headers = signed_headers(url, email=self.admin.email, role=self.admin.role,
            scope=self.admin.scope, method="POST", body=raw, request_id="promotion-ui-disabled-writer")
        before = m.AiReportRun.objects.count()
        with (patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}),
              djtest.override_settings(DJANGO_INTERNAL_SECRET=TEST_SECRET,
                  DJANGO_PROCESS_ROLE="ai_writer", AI_PROMOTION_AGENT_RUNTIME_ENABLED=False),
              patch("ai_assistant.views.authority"),
              patch("ai_assistant.provider.turn", side_effect=AssertionError("no paid model"))):
            response = self.client.post(url, data=raw, content_type="application/json", headers=headers)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(m.AiReportRun.objects.count(), before)

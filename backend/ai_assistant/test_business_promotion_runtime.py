"""Actual sealed PostgreSQL roots for an unregistered promotion proposal."""
from copy import deepcopy
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from . import business_diagnostic_screening as screening
from . import business_promotion_runtime as service
from . import business_screening_store
from . import test_business_promotion_views as fixtures
from .policy import AiError, canonical, digest


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessPromotionRuntimeTests(djtest.TransactionTestCase):
    user = fixtures.BusinessPromotionViewTests.user
    call = fixtures.BusinessPromotionViewTests.call
    bundle = fixtures.BusinessPromotionViewTests.bundle
    input_for = fixtures.BusinessPromotionViewTests.input_for
    insert = fixtures.BusinessPromotionViewTests.insert
    seed = fixtures.BusinessPromotionViewTests.seed
    collect_body = fixtures.BusinessPromotionViewTests.collect_body
    ad = fixtures.BusinessPromotionViewTests.ad

    def setUp(self):
        fixtures.BusinessPromotionViewTests.setUp(self)
        body = deepcopy(self.fixed_body)
        body["clientRequestId"] = "promotion-runtime-sealed"
        # A fixed analysisRequest requires every non-master source identity to
        # have the same complete window set. The existing owning fixture adds
        # only ads-previous; complete sales and the other-shop negative source.
        by_key = {source["key"]:source for source in body["sources"]}
        for key in ("sales", "ads-other"):
            previous = deepcopy(by_key[key])
            previous["key"] = key + "-previous"
            previous["query"]["window"] = "previous"
            body["sources"].append(previous)
        body["analysisRequest"] = {"schemaVersion":"business-analysis-request-v1",
            "question":"固定推广范围筛查", "requestedDimensions":["shop","sku","spu"],
            "requestedWindows":["current","previous"]}
        self.parent = self.collect_body(body)
        self.report, _ = self.seed()
        verified = screening.prepare_for_report(self.report.id, self.admin)
        self.screening_id = business_screening_store.publish(verified, self.admin)["reference"]["id"]
        self.selector = {"sourceKey":"ads", "baselineKey":"ads-previous"}

    def candidate(self, **selector):
        return service.prepare_candidate(self.report.id, self.screening_id,
            {**self.selector, **selector}, self.admin)

    def test_real_roots_baseline_graph_and_old_bytes_are_read_only(self):
        before = (self.report.snapshot_json, self.report.workflow.input_json,
            list(self.parent.__class__.objects.filter(pk=self.parent.pk).values_list("plan_json","state_json"))[0])
        with patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote, CaptureQueriesContext(connection) as queries:
            result = self.candidate()
            same = service.checked_candidate(self.report.id, self.screening_id, self.selector, result, self.admin)
        self.assertEqual(result, same)
        self.assertEqual(result["candidateDigest"], digest({k:v for k,v in result.items() if k != "candidateDigest"}))
        self.assertEqual(result["origins"]["reportProfile"], "business-agent-integrated-reference-v1")
        self.assertEqual(result["proposedSnapshot"]["promotionSelector"],
            {"sourceKey":"ads", "baselineKey":"ads-previous", "views":["keyword_sku", "keyword_sku_context"]})
        self.assertEqual([node["key"] for node in result["proposedGraph"]["nodes"]],
            ["commerce", "promotion", "market_b2b", "independent_review", "report", "human_review"])
        self.assertFalse(result["authorityVerified"])
        self.assertFalse(result["registered"])
        self.assertEqual(result["readiness"], "requires_new_persistent_profile")
        self.assertEqual(before, (self.report.__class__.objects.get(pk=self.report.pk).snapshot_json,
            self.report.workflow.__class__.objects.get(pk=self.report.workflow.pk).input_json,
            list(self.parent.__class__.objects.filter(pk=self.parent.pk).values_list("plan_json","state_json"))[0]))
        model.assert_not_called(); remote.assert_not_called()
        self.assertTrue(queries.captured_queries)
        for query in queries:
            self.assertFalse(query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")))
            self.assertNotIn("netshop_rows", query["sql"].lower())
            self.assertNotIn("sales_order_lines", query["sql"].lower())

    def test_wrong_source_report_screening_actor_and_candidate_rejected(self):
        for choice in ({"sourceKey":"sales"}, {"sourceKey":"missing"},
                {"baselineKey":"ads-other"}, {"baselineKey":"ads"}):
            with self.subTest(choice=choice), self.assertRaises(AiError): self.candidate(**choice)
        with self.assertRaises(AiError):
            service.prepare_candidate("missing-report", self.screening_id, self.selector, self.admin)
        other_report, _ = self.seed()
        with self.assertRaises(AiError):
            service.prepare_candidate(other_report.id, self.screening_id, self.selector, self.admin)
        with self.assertRaises(AiError):
            service.prepare_candidate(self.report.id, "missing-screening", self.selector, self.admin)
        for principal in (self.viewer, self.user("other-promotion-runtime@example.invalid", "admin", None)):
            with self.assertRaises(AiError):
                service.prepare_candidate(self.report.id, self.screening_id, self.selector, principal)
        actual = self.candidate()
        for altered in (lambda v: v["origins"].update(screeningManifestDigest="0"*64),
                lambda v: v["proposedSnapshot"]["promotionSelector"].update(baselineKey="ads-other"),
                lambda v: v.update(authorityVerified=True)):
            forged = deepcopy(actual); altered(forged)
            with self.assertRaises(AiError):
                service.checked_candidate(self.report.id, self.screening_id, self.selector, forged, self.admin)

    def test_late_revocation_and_changed_root_never_return_candidate(self):
        from access_control.models import AppUser
        original = service._roots
        calls = []
        def changed(*args, **kwargs):
            value = original(*args, **kwargs)
            calls.append(value)
            if len(calls) == 2:
                value[2]["screeningManifestDigest"] = "0"*64
            return value
        with patch.object(service, "_roots", changed), self.assertRaises(AiError): self.candidate()
        def revoked(*args, **kwargs):
            value = original(*args, **kwargs)
            if value is not None and not getattr(revoked, "done", False):
                revoked.done = True
                AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            return value
        with patch.object(service, "_roots", revoked), self.assertRaises(AiError): self.candidate()

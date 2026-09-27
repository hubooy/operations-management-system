"""Prospective persistent shape; real sealed roots are tested on isolated PG."""
from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from . import business_promotion_creation_contract as service
from . import business_promotion_runtime_contract as promotion, models as m
from . import test_business_diagnostic_screening as fixtures
from .policy import AiError, canonical, digest


def catalog():
    entries = []
    for name in promotion.TOOL_ORDER:
        entries.append({"name": name, "title": name, "description": "只读固定报告",
            "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            "annotations": {"readOnlyHint": True, "destructiveHint": False,
                "idempotentHint": True, "openWorldHint": False},
            "risk": "read_only", "allowedRoles": ["admin"], "scopePolicy": "unscoped_only",
            "execution": {"environment": "worker_inline", "mode": "direct",
                "allowedSurfaces": [promotion.SURFACE], "timeoutMs": 12000,
                "maxResultCharacters": 40000, "maxCallsPerRequest": 8}})
    return entries


class CreationShapePureTests(unittest.TestCase):
    def test_exact_four_catalog_entries_and_surface_fail_closed(self):
        self.assertEqual(service._catalog(catalog()), catalog())
        for changed in ([], catalog()[:3], list(reversed(catalog())),
                        [*catalog(), catalog()[0]]):
            with self.assertRaises(AiError): service._catalog(changed)
        for change in (lambda e: e[0]["execution"].update(allowedSurfaces=["ai_chat"]),
                       lambda e: e[0].update(allowedRoles=["viewer"]),
                       lambda e: e[0]["execution"].update(maxCallsPerRequest=8.0),
                       lambda e: e[0]["inputSchema"].update(additionalProperties=True)):
            entries = catalog(); change(entries)
            with self.assertRaises(AiError): service._catalog(entries)

    def test_request_rejects_unknown_fields_null_baseline_and_implicit_budget(self):
        body = {"reportId": "r-1", "screeningId": "s-1", "question": "看词货", "sourceKey": "ads"}
        self.assertEqual(service._request(body), body)
        for changed in ({**body, "baselineKey": None}, {**body, "extra": True},
                        {**body, "mappingPairs": None}, {**body, "budgetPlan": None},
                        {**body, "reportId": "bad id"}, {**body, "question": ""}):
            with self.assertRaises((AiError, ValueError)): service._request(changed)
        with self.assertRaises(AiError): service.PreparedCandidate(None, {}, "run", None, [], {})


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionCreationPgTests(TransactionTestCase):
    user = fixtures.DiagnosticScreeningTests.user
    call = fixtures.DiagnosticScreeningTests.call
    collect_body = fixtures.DiagnosticScreeningTests.collect_body
    bundle = fixtures.DiagnosticScreeningTests.bundle
    input_for = fixtures.DiagnosticScreeningTests.input_for
    insert = fixtures.DiagnosticScreeningTests.insert
    seed = fixtures.DiagnosticScreeningTests.seed
    setUp = fixtures.DiagnosticScreeningTests.setUp

    def request(self, tag, *, mapped=False, budget=False):
        value = {"reportId": "proposed-promotion-"+tag, "screeningId": "proposed-screen-"+tag,
            "question": "固定经营词货诊断", "sourceKey": "ads"}
        if mapped: value["mappingPairs"] = [{"salesKey": "sales", "masterKey": "master"}]
        if budget: value["budgetPlan"] = deepcopy(self.budget_plan)
        return value

    @staticmethod
    def counts():
        return tuple(model.objects.count() for model in
            (m.AiReportRun, m.AiWorkflowRuns, m.AiBusinessScreeningRun, m.AiBusinessBudgetPlan))

    def test_actual_sealed_shape_mapping_budget_graph_and_zero_write(self):
        for mapped, budget in ((False, False), (True, True)):
            with self.subTest(mapped=mapped, budget=budget):
                request = self.request(f"{int(mapped)}-{int(budget)}", mapped=mapped, budget=budget)
                before = self.counts()
                with patch.object(service.transport, "catalog", return_value=catalog()), CaptureQueriesContext(connection) as queries:
                    prepared = service.prepare_candidate(self.parent.id, request, self.admin)
                    shape = service.revalidate_candidate(prepared, self.admin)
                self.assertEqual(self.counts(), before)
                self.assertFalse(any(item["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
                                     for item in queries))
                snap, flow = shape["snapshot"], shape["workflowInput"]
                self.assertEqual((shape["ownerEmail"], shape["scopeJson"]), (self.admin.email.lower(), "null"))
                self.assertEqual((shape["authorityVerified"], shape["registered"]), (False, False))
                self.assertEqual(snap["executionProfile"], promotion.PROFILE)
                self.assertEqual(snap["screeningIntent"], flow["screeningIntent"])
                self.assertEqual(snap["promotionSelector"], flow["promotionRef"]["promotionSelector"])
                self.assertEqual(snap["contextDigest"], flow["promotionRef"]["contextDigest"])
                self.assertEqual(snap["sealedDigest"], flow["promotionRef"]["sealedDigest"])
                self.assertEqual(snap["catalogDigest"], flow["promotionRef"]["catalogDigest"])
                self.assertEqual(snap["promotionCatalogDigest"], flow["promotionRef"]["promotionCatalogDigest"])
                self.assertNotEqual(snap["catalogDigest"], snap["promotionCatalogDigest"])
                self.assertEqual(snap["promotionAlgorithmVersion"], promotion.ALGORITHM_VERSION)
                self.assertEqual(("mappingPlan" in snap, "budgetRef" in snap), (mapped, budget))
                self.assertEqual(("mappingRef" in flow, "budgetRef" in flow), (mapped, budget))
                self.assertEqual([node["key"] for node in shape["graph"]["nodes"]],
                    ["commerce", "promotion", "market_b2b", "independent_review", "report", "human_review"])
                self.assertEqual(shape["allowedTools"], list(promotion.TOOL_ORDER))
                self.assertEqual(shape["toolCatalogDigest"], digest(catalog()))
                for key, value in (("snapshot", snap), ("workflowInput", flow),
                                   ("graph", shape["graph"]), ("allowedTools", shape["allowedTools"])):
                    raw = canonical(value).encode("utf-8")
                    self.assertEqual(shape["canonicalBytes"][key], len(raw))
                    self.assertEqual(shape["digests"][key], hashlib.sha256(raw).hexdigest())

    def test_wrong_actor_source_base_collision_and_late_catalog_change(self):
        before = self.counts()
        other = self.user("promotion-other@example.invalid", "admin", None)
        with patch.object(service.transport, "catalog", return_value=catalog()):
            with self.assertRaises(AiError):
                service.prepare_candidate(self.parent.id, self.request("other"), other)
            for source in ("missing", "sales", "master"):
                with self.assertRaises(AiError):
                    service.prepare_candidate(self.parent.id, {**self.request(source), "sourceKey": source}, self.admin)
            with self.assertRaises(AiError):
                service.prepare_candidate(self.parent.id,
                    {**self.request("badbase"), "baselineKey": "sales"}, self.admin)
            with self.assertRaises(AiError):
                service.prepare_candidate(self.parent.id,
                    {**self.request("old-report"), "reportId": self.report.id}, self.admin)
            prepared = service.prepare_candidate(self.parent.id, self.request("fixed"), self.admin)
        changed = catalog(); changed[0]["description"] = "目录内容变化"
        with patch.object(service.transport, "catalog", return_value=changed), self.assertRaises(AiError):
            service.revalidate_candidate(prepared, self.admin)
        self.assertEqual(self.counts(), before)

    def test_revalidation_rejects_forged_shape_and_old_evidence_version(self):
        with patch.object(service.transport, "catalog", return_value=catalog()):
            prepared = service.prepare_candidate(self.parent.id, self.request("tamper"), self.admin)
            old = prepared._shape_json
            modified = json.loads(old); modified["snapshot"]["contextDigest"] = "0"*64
            object.__setattr__(prepared, "_shape_json", canonical(modified))
            with self.assertRaises(AiError): service.revalidate_candidate(prepared, self.admin)
            object.__setattr__(prepared, "_shape_json", old)
            with patch.object(service.evidence_store, "assert_current", side_effect=AiError("stale", "version_conflict", 409)):
                with self.assertRaises(AiError): service.revalidate_candidate(prepared, self.admin)

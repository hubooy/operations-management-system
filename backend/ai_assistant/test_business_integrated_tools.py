"""Real sealed facts and inert fixed reports, without opening model runtime."""
from copy import deepcopy
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from business_analysis import mapped_results
from . import business_budget_store, business_evidence as evidence
from . import business_integrated as contract, business_integrated_tools as tools
from . import business_mapped_analysis, test_business_integrated_guard as guard_fixtures
from .business_sealed import Reader
from .policy import AiError, canonical, digest, mutation


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessIntegratedToolsTests(djtest.TransactionTestCase):
    # Import the fixture module, not its TestCase class into discovery globals.
    user = guard_fixtures.BusinessIntegratedGuardTests.user
    call = guard_fixtures.BusinessIntegratedGuardTests.call
    collect_body = guard_fixtures.BusinessIntegratedGuardTests.collect_body
    insert = guard_fixtures.BusinessIntegratedGuardTests.insert

    def setUp(self):
        guard_fixtures.BusinessIntegratedGuardTests.setUp(self)

    def seed(self, *, budget=False, choices=None, input_suffix=""):
        self.serial += 1
        report_id = f"integrated-tools-{self.serial}"
        fixed = business_budget_store.prepare(self.parent, self.budget_plan, self.admin, report_id) if budget else None
        prepared = contract.prepare(self.parent, choices or [{"salesKey":"sales", "masterKey":"master"}],
            self.admin, report_id, "完整封存商品关联与预算，不调用模型", budget=fixed)
        self.assertEqual(contract.revalidate(prepared, self.admin), prepared)
        with mutation(self.admin):
            report = self.insert((prepared.snapshot, prepared.reference, fixed), input_raw=prepared.reference_json+input_suffix)
        return report, prepared

    def read(self, report, operation, actor=None, **params):
        return tools.read(report.id, operation, {"runId":self.parent.id, **params}, actor or self.admin)

    def verified(self, result):
        self.assertLessEqual(len(canonical(result).encode("utf-8")), 38000)
        self.assertEqual(result["pageDigest"], digest({k:v for k,v in result.items() if k != "pageDigest"}))
        return result

    def test_directory_fixed_pairs_reference_and_optional_budget_mode(self):
        for budget in (False, True):
            with self.subTest(budget=budget):
                report, prepared = self.seed(budget=budget)
                page = self.verified(self.read(report, "directory"))
                self.assertEqual(page["reference"], prepared.reference)
                self.assertEqual(page["budgetMode"], "fixed" if budget else "none")
                self.assertEqual((page["total"], page["returned"], page["nextOffset"]), (3, 3, None))
                entries = {entry["key"]:entry for entry in page["items"]}
                self.assertEqual(entries["sales"]["mappingPair"], prepared.plan["pairs"][0])
                self.assertIsNone(entries["master"]["mappingPair"])
                self.assertIsNone(entries["ads"]["mappingPair"])
                self.assertTrue(all(entry["queryDigest"] == digest(entry["query"]) for entry in entries.values()))

    def test_full_directory_follows_actual_prefix_without_skipping_sources(self):
        body = deepcopy(self.evidence_body)
        body["clientRequestId"] = "integrated-wide-directory"
        for index in range(22):
            body["sources"].append({"key":f"sales_{index:02}", "domain":"sales",
                "query":{**self.query, "channel":"精确合成渠道"+str(index)}})
        self.parent = self.collect_body(body)
        choices = [{"salesKey":source["key"], "masterKey":"master"} for source in body["sources"] if source["domain"] == "sales"]
        report, prepared = self.seed(choices=choices)
        expected = [source["key"] for source in Reader(self.parent, self.admin).sources]
        actual, offset, page_count = [], 0, 0
        while offset is not None:
            page = self.verified(self.read(report, "directory", offset=str(offset)))
            self.assertEqual(page["offset"], offset)
            actual.extend(entry["key"] for entry in page["items"])
            self.assertEqual(page["returned"], len(page["items"]))
            self.assertTrue(all(entry["mappingPair"] is not None for entry in page["items"] if entry["domain"] == "sales"))
            offset, page_count = page["nextOffset"], page_count+1
            self.assertLessEqual(page_count, 3)
        self.assertEqual((actual, page_count), (expected, 2))
        sources = Reader(self.parent, self.admin).sources
        # Lower the byte budget only; use the same real entries and complete serializer.
        full = tools.directory_from(prepared, self.parent, sources)
        prefix = deepcopy(full); prefix["items"] = prefix["items"][:1]
        prefix.update(returned=1, nextOffset=1)
        prefix["pageDigest"] = digest({k:v for k,v in prefix.items() if k != "pageDigest"})
        cap = len(canonical(prefix).encode("utf-8"))
        with patch.object(tools, "MAX_RESPONSE_BYTES", cap):
            result = tools.directory_from(prepared, self.parent, sources)
            self.assertEqual(result, prefix)
        with patch.object(tools, "MAX_RESPONSE_BYTES", cap-1), self.assertRaises(AiError) as too_big:
            tools.directory_from(prepared, self.parent, sources)
        self.assertEqual(too_big.exception.status, 413)

    def test_native_and_mapped_compute_real_values_without_external_reads_or_writes(self):
        report, prepared = self.seed()
        native_before = evidence.analysis_table(self.parent.id, {"sourceKey":"sales", "dimension":"sku"}, self.admin)
        pair = prepared.plan["pairs"][0]["pairKey"]
        with patch("ai_assistant.provider.turn") as model, patch("ai_assistant.transport.execute_tool") as remote, CaptureQueriesContext(connection) as queries:
            native = self.verified(self.read(report, "analysis", mode="native", sourceKey="sales", dimension="sku"))["table"]
            mapped = self.verified(self.read(report, "analysis", mode="mapped", pairKey=pair, dimension="sku"))["table"]
        self.assertEqual(native["rows"], native_before["rows"])
        self.assertTrue(all(row["dimensionMissing"] for row in native["rows"]))
        self.assertEqual(sum(row["currentRowCount"] for row in mapped["rows"]), 12)
        self.assertEqual(sum(row["metrics"]["netSalesCents"]["value"] for row in mapped["rows"]), 120000)
        self.assertEqual({row["entity"]["mappingStatus"] for row in mapped["rows"]}, {"matched", "unmatched"})
        matched = next(row for row in mapped["rows"] if row["entity"]["mappingStatus"] == "matched")
        self.assertEqual((matched["entity"]["skuId"], matched["currentRowCount"]), ("SKU1", 11))
        self.assertEqual(set(matched["metrics"]), set(mapped_results.METRICS))
        with business_mapped_analysis.table(self.parent.id, prepared.plan, pair, "sku", self.admin) as table:
            self.assertEqual(mapped["rows"], table.page()["rows"])
        model.assert_not_called(); remote.assert_not_called()
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
        self.assertFalse(any(any(name in q["sql"].lower() for name in ("netshop_rows", "sales_order_lines")) for q in queries))

    def test_analysis_strict_modes_identity_offsets_and_no_cross_fields(self):
        report, prepared = self.seed()
        pair = prepared.plan["pairs"][0]["pairKey"]
        valid = {"mode":"native", "sourceKey":"ads", "dimension":"sku"}
        invalid = [{"pairKey":pair}, {"baselinePairKey":pair}, {"sourceKey":"missing"}, {"mode":"unsupported"},
            {"dimension":"unsupported"}, {"offset":"01"}, {"offset":True}, {"offset":0}, {"offset":"250001"},
            {"offset":"-1"}, {"limit":"20"}, {"unexpected":"x"}, {"runId":"another-run"}]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(AiError):
                self.read(report, "analysis", **{**valid, **changes})
        for params in ({"mode":"native", "dimension":"sku"},
                {"mode":"mapped", "dimension":"sku"},
                {"mode":"mapped", "dimension":"sku", "pairKey":pair, "baselineKey":"sales"},
                {"mode":"mapped", "dimension":"sku", "pairKey":pair, "sourceKey":"sales"},
                {"mode":"mapped", "dimension":"keyword", "pairKey":pair},
                {"mode":"mapped", "dimension":"sku", "pairKey":"f"*64},
                {"mode":"mapped", "dimension":"sku", "pairKey":pair, "baselinePairKey":pair}):
            with self.subTest(params=params), self.assertRaises(AiError): self.read(report, "analysis", **params)
        for operation, bad in (("directory", "48"), ("budget", "100")):
            with self.subTest(operation=operation), self.assertRaises(AiError): self.read(report, operation, offset=bad)

    def test_native_and_mapped_byte_prefixes_keep_complete_rows_and_digests(self):
        report, prepared = self.seed()
        for params in ({"mode":"native", "sourceKey":"ads", "dimension":"sku"},
                {"mode":"mapped", "pairKey":prepared.plan["pairs"][0]["pairKey"], "dimension":"sku"}):
            with self.subTest(mode=params["mode"]):
                full = self.read(report, "analysis", **params)
                self.assertGreaterEqual(len(full["table"]["rows"]), 2)
                first_table = {**full["table"], "rows":full["table"]["rows"][:1]}
                expected = tools._table_page(prepared, params["mode"], full["selector"], first_table, 0)
                cap = len(canonical(expected).encode("utf-8"))
                with patch.object(tools, "MAX_RESPONSE_BYTES", cap):
                    first = self.read(report, "analysis", **params)
                self.assertEqual(first, expected)
                self.assertEqual(first["table"]["pagination"]["nextOffset"], 1)
                rest = self.read(report, "analysis", **params, offset="1")
                self.assertEqual(first["table"]["rows"]+rest["table"]["rows"], full["table"]["rows"])
                if "pageDigest" in first["table"]:
                    self.assertEqual(first["table"]["pageDigest"], digest({k:v for k,v in first["table"].items() if k != "pageDigest"}))
                with patch.object(tools, "MAX_RESPONSE_BYTES", cap-1), self.assertRaises(AiError) as too_big:
                    self.read(report, "analysis", **params)
                self.assertEqual(too_big.exception.status, 413)

    def test_fixed_budget_is_recomputed_bound_and_absence_is_not_empty_success(self):
        empty, _ = self.seed()
        with self.assertRaisesRegex(AiError, "没有固定推广预算"): self.read(empty, "budget")
        report, prepared = self.seed(budget=True)
        page = self.verified(self.read(report, "budget"))
        result = page["budget"]
        self.assertEqual(page["reference"], prepared.reference)
        self.assertEqual(result["budgetRef"], prepared.budget.reference)
        self.assertEqual(result["binding"], prepared.budget.binding)
        self.assertEqual(result["allocation"]["allocatedCents"], 9000)
        self.assertEqual([row["budgetCents"] for row in result["rows"]], [6000, 3000])
        self.assertEqual(result["scenarios"][0]["summary"]["projectedAttributedGmvCents"], 45000)
        self.assertEqual(result["scenarios"][1]["summary"]["projectedAttributedGmvCents"], 27000)
        self.assertEqual(result["pagination"]["nextOffset"], None)
        self.assertEqual(self.read(report, "budget", offset="1")["budget"]["rows"], result["rows"][1:])
        with self.assertRaises(AiError): self.read(report, "budget", offset="2")

    def test_owner_prepared_plan_seal_and_exact_workflow_binding_fail_closed(self):
        report, prepared = self.seed()
        for actor in (self.viewer, self.user("integrated-other@example.invalid", "admin", None)):
            for operation in ("directory", "analysis", "budget"):
                with self.subTest(actor=actor.email, operation=operation), self.assertRaises(AiError):
                    self.read(report, operation, actor, **({"mode":"native", "sourceKey":"sales", "dimension":"shop"} if operation == "analysis" else {}))
        changed = prepared.snapshot; changed["mappingPlan"]["pairs"][0]["masterKey"] = "ads"
        changed["mappingPlanDigest"] = digest(changed["mappingPlan"])
        forged = contract.Prepared(prepared.owner_email, prepared.scope_json, canonical(changed), prepared.reference_json)
        with self.assertRaises(AiError): contract.revalidate(forged, self.admin)
        changed_report = deepcopy(report); changed_report.snapshot_json = canonical(changed)
        with self.assertRaises(AiError): contract.bound(changed_report, self.admin)
        # DB allows harmless outer JSON whitespace, but the service requires exact fixed input bytes.
        noncanonical, _ = self.seed(input_suffix=" ")
        with self.assertRaisesRegex(AiError, "工作流固定引用"): self.read(noncanonical, "directory")
        with patch("ai_assistant.business_evidence_store.verify_seal", side_effect=AiError("synthetic seal rejected")), self.assertRaises(AiError):
            self.read(report, "directory")

    def test_late_scan_failure_or_permission_revocation_cannot_return_results(self):
        report, _ = self.seed()
        original = Reader.pages
        def late(reader, key, checkpoint=None):
            yield from original(reader, key, checkpoint)
            raise AiError("synthetic broken final chain", "conflict", 409)
        with patch.object(Reader, "pages", late), self.assertRaises(AiError):
            self.read(report, "analysis", mode="native", sourceKey="ads", dimension="sku")
        from access_control.models import AppUser
        original_page = tools._table_page
        def revoke(*args, **kwargs):
            result = original_page(*args, **kwargs)
            AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            return result
        with patch.object(tools, "_table_page", revoke), self.assertRaises(AiError):
            self.read(report, "analysis", mode="native", sourceKey="ads", dimension="sku")

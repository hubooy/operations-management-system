from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

from . import volume_plan
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, digest
from .volume_plan import build, verify, REQUEST_SCHEMA


def fixture(count=1, rows=1):
    return {"schemaVersion": REQUEST_SCHEMA, "reportId": "report-test", "evidenceDigest": "a" * 64,
            "rendererVersion": 4, "tables": [{"key": f"table-{i}", "title": f"表{i}", "rowCount": rows,
                                             "columnCount": 8} for i in range(count)]}


def fragments(plan):
    return [table for volume in plan["volumes"] for table in volume["tables"]]


class VolumePlanTests(TestCase):
    def test_131_and_156_tables_keep_all_dimensions_in_order(self):
        for count in (131, 156):
            for budget in (0, 3):
                with self.subTest(count=count, budget=budget):
                    request = fixture(count)
                    original = deepcopy(request)
                    result = build(request, native_budget_sheets=budget)
                    self.assertTrue(verify(result, request, native_budget_sheets=budget))
                    self.assertEqual(request, original)
                    self.assertEqual(result["volumeCount"], 2)
                    self.assertEqual([len(v["tables"]) for v in result["volumes"]], [120-budget, count-120+budget])
                    self.assertEqual([t["key"] for t in fragments(result)], [t["key"] for t in request["tables"]])
                    self.assertEqual([v["nativeBudgetSheets"] for v in result["volumes"]], [budget, 0])
                    self.assertEqual(result["byteCapacity"], "requires_actual_render_verification")
                    self.assertEqual(result["planDigest"], build(request, native_budget_sheets=budget)["planDigest"])

    def test_million_row_boundaries_and_empty_visible_table(self):
        request = fixture(5)
        for table, rows in zip(request["tables"], (0, 999999, 1000000, 1000001, 2000000)):
            table["rowCount"] = rows
        plan = build(request)
        spans = [(p["key"], p["rowOffset"], p["rowLimit"]) for p in fragments(plan)]
        self.assertEqual(spans, [("table-0", 0, 0), ("table-1", 0, 999999), ("table-2", 0, 1000000),
                                ("table-3", 0, 1000000), ("table-3", 1000000, 1),
                                ("table-4", 0, 1000000), ("table-4", 1000000, 1000000)])
        self.assertEqual(plan["fragmentCount"], 7)
        self.assertEqual(plan["totalRows"], 5000000)
        self.assertTrue(verify(plan, request))
        self.assertEqual(fragments(plan)[0]["fragmentCount"], 1)
        self.assertEqual(len({p["fragmentKey"] for p in fragments(plan)}), 7)

    def test_budget_only_first_volume_and_budget_only_report(self):
        request = fixture(1, 5)
        plan = build(request, max_tables=3, max_rows=2, native_budget_sheets=3)
        self.assertEqual(plan["volumeCount"], 2)
        self.assertEqual(plan["volumes"][0], {"volumeIndex": 1, "volumeCount": 2, "nativeBudgetSheets": 3,
                                              "kind": "budget_only", "tables": []})
        self.assertEqual([p["rowLimit"] for p in plan["volumes"][1]["tables"]], [2, 2, 1])
        self.assertTrue(verify(plan, request, max_tables=3, max_rows=2, native_budget_sheets=3))
        empty = fixture(0)
        budget = build(empty, max_tables=3, native_budget_sheets=3)
        self.assertEqual(budget["volumeCount"], 1)
        self.assertEqual(budget["sourceTableCount"], 0)
        self.assertEqual(budget["volumes"][0]["kind"], "budget_only")
        with self.assertRaises(AnalysisContractError):
            build(empty)
        with self.assertRaises(AnalysisContractError):
            build(request, max_tables=2, native_budget_sheets=3)

    def test_fragment_crosses_volumes_without_reordering_or_budget_duplication(self):
        request = fixture(3)
        request["tables"][0]["rowCount"] = 7
        request["tables"][1]["rowCount"] = 0
        plan = build(request, max_rows=2, max_tables=4, native_budget_sheets=3)
        self.assertEqual([len(v["tables"]) for v in plan["volumes"]], [1, 4, 1])
        parts = fragments(plan)
        self.assertEqual([p["key"] for p in parts], ["table-0"]*4 + ["table-1", "table-2"])
        self.assertEqual([p["fragmentIndex"] for p in parts[:4]], [1, 2, 3, 4])
        self.assertEqual([p["rowOffset"] for p in parts[:4]], [0, 2, 4, 6])
        self.assertEqual(sum(v["nativeBudgetSheets"] for v in plan["volumes"]), 3)
        self.assertTrue(all(v["volumeCount"] == 3 for v in plan["volumes"]))

    def test_exact_volume_limit_and_no_partial_plan_for_excess_rows(self):
        self.assertEqual(build(fixture(12000))["volumeCount"], 100)
        with self.assertRaises(AnalysisContractError):
            build(fixture(12000), native_budget_sheets=3)
        with self.assertRaises(AnalysisContractError):
            build(fixture(12001))
        with self.assertRaises(AnalysisContractError):
            build(fixture(1, MAX_SAFE_INTEGER))
        self.assertEqual(build(fixture(120))["volumeCount"], 1)
        with self.assertRaises(AnalysisContractError):
            build(fixture(120), max_volumes=1, native_budget_sheets=3)

    def test_duplicate_illegal_numbers_unsupported_schema_and_total_overflow(self):
        mutations = [lambda r: r.update(schemaVersion="unknown"), lambda r: r.update(extra=True),
                     lambda r: r.update(reportId="../report"), lambda r: r.update(evidenceDigest="A"*64),
                     lambda r: r.update(rendererVersion=True), lambda r: r.update(rendererVersion=0),
                     lambda r: r.update(tables={}), lambda r: r["tables"].append(deepcopy(r["tables"][0])),
                     lambda r: r["tables"][0].update(rowCount=True), lambda r: r["tables"][0].update(rowCount=1.0),
                     lambda r: r["tables"][0].update(rowCount=-1), lambda r: r["tables"][0].update(rowCount=MAX_SAFE_INTEGER+1),
                     lambda r: r["tables"][0].update(columnCount=161), lambda r: r["tables"][0].update(columnCount=0),
                     lambda r: r["tables"][0].update(columnCount=False), lambda r: r["tables"][0].update(title=""),
                     lambda r: r["tables"][0].update(key=" key"), lambda r: r["tables"][0].update(key="key\0"),
                     lambda r: r["tables"][0].update(title="\ud800"), lambda r: r["tables"][0].update(rowSpans=[])]
        for mutate in mutations:
            request = fixture()
            mutate(request)
            with self.subTest(request=repr(request)), self.assertRaises(AnalysisContractError):
                build(request)
        overflow = fixture(2)
        overflow["tables"][0]["rowCount"] = MAX_SAFE_INTEGER
        with self.assertRaisesRegex(AnalysisContractError, "总行数"):
            build(overflow)
        for kwargs in ({"max_tables": 121}, {"max_tables": True}, {"max_rows": 1000001}, {"max_rows": 0},
                       {"max_volumes": 101}, {"max_volumes": False}, {"native_budget_sheets": True}, {"native_budget_sheets": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(AnalysisContractError):
                build(fixture(), **kwargs)

    def test_verifier_rejects_missing_overlap_reorder_source_swap_and_redigested_tampering(self):
        request = fixture(3, 5)
        plan = build(request, max_rows=2)
        mutations = [lambda p: p["volumes"][0]["tables"].pop(),
                     lambda p: p["volumes"][0]["tables"][1].update(rowOffset=1),
                     lambda p: p["volumes"][0]["tables"][1].update(rowLimit=1),
                     lambda p: p["volumes"][0]["tables"].reverse(),
                     lambda p: p["volumes"][0]["tables"][0].update(key="stolen-table"),
                     lambda p: p["tables"][0].update(title="替换来源"),
                     lambda p: p.update(reportId="another-report"), lambda p: p.update(evidenceDigest="b"*64),
                     lambda p: p.update(rendererVersion=3), lambda p: p.update(schemaVersion="unknown"),
                     lambda p: p.update(totalRows=14), lambda p: p.update(volumeCount=2),
                     lambda p: p["volumes"][0].update(volumeIndex=2),
                     lambda p: p["volumes"][0].update(nativeBudgetSheets=3),
                     lambda p: p.update(byteCapacity="verified"), lambda p: p["capacity"].update(maxRows=3)]
        for mutate in mutations:
            broken = deepcopy(plan)
            mutate(broken)
            broken["planDigest"] = digest({k: v for k, v in broken.items() if k != "planDigest"})
            with self.subTest(broken=broken), self.assertRaises(AnalysisContractError):
                verify(broken, request, max_rows=2)
        broken = deepcopy(plan)
        broken["planDigest"] = "0"*64
        with self.assertRaisesRegex(AnalysisContractError, "摘要"):
            verify(broken, request, max_rows=2)
        with self.assertRaises(AnalysisContractError):
            verify(plan, request)  # Capacity policy is a trusted verification input.

    def test_empty_table_cannot_be_dropped_even_with_forged_valid_digest(self):
        request = fixture(2, 0)
        plan = build(request)
        broken = deepcopy(plan)
        broken["volumes"][0]["tables"].pop()
        broken["tables"].pop()
        broken.update(sourceTableCount=1, fragmentCount=1)
        broken["planDigest"] = digest({k: v for k, v in broken.items() if k != "planDigest"})
        with self.assertRaises(AnalysisContractError):
            verify(broken, request)
        self.assertTrue(verify(plan, request))

    def test_verifier_rejects_oversized_untrusted_structures_before_hashing(self):
        request = fixture()
        plan = build(request)
        mutations = [
            lambda p: p.update(volumes=p["volumes"] * 101),
            lambda p: p["volumes"][0].update(tables=p["volumes"][0]["tables"] * 12001),
            lambda p: p.update(tables=p["tables"] * 12001),
            lambda p: p["tables"][0].update(title="长" * 100000),
            lambda p: p["volumes"][0]["tables"][0].update(fragmentKey="x" * 100000),
            lambda p: p.update(planDigest="0" * 100000),
            lambda p: p["capacity"].update(extra_fields=[0] * 12001),
        ]
        for mutate in mutations:
            broken = deepcopy(plan)
            mutate(broken)
            supplied_ids = {id(broken), id(broken["tables"]), id(broken["volumes"]), id(broken["capacity"])}

            def bounded_digest(value):
                if id(value) in supplied_ids or type(value) is dict and any(id(child) in supplied_ids for child in value.values()):
                    self.fail("Verifier attempted to hash an untrusted oversized structure")
                return digest(value)

            with self.subTest(mutation=mutate), patch.object(volume_plan, "digest", side_effect=bounded_digest):
                with self.assertRaises(AnalysisContractError):
                    verify(broken, request)

    def test_verifier_rejects_nested_substitute_types_and_numeric_equivalence(self):
        request = fixture()
        plan = build(request)
        mutations = [lambda p: p.update(volumeCount=True), lambda p: p.update(volumeCount=1.0),
                     lambda p: p["volumes"][0].update(volumeIndex=True),
                     lambda p: p["volumes"][0]["tables"][0].update(rowOffset=False),
                     lambda p: p["tables"][0].update(rowCount=1.0),
                     lambda p: p.update(volumes=tuple(p["volumes"])),
                     lambda p: p["volumes"][0].update(tables={"0": p["volumes"][0]["tables"][0]}),
                     lambda p: p["tables"][0].update(title={"nested": ["表0"]}),
                     lambda p: p.update(totalRows=[[[1]]])]
        for mutate in mutations:
            broken = deepcopy(plan)
            mutate(broken)
            # Even a correctly rehashed altered manifest must fail typed matching.
            broken["planDigest"] = digest({k: v for k, v in broken.items() if k != "planDigest"})
            with self.subTest(mutation=mutate), self.assertRaises(AnalysisContractError):
                verify(broken, request)

        class ExplosiveString(str):
            def __eq__(self, other):
                raise AssertionError("Untrusted subclass comparison must not run")

        broken = deepcopy(plan)
        broken["schemaVersion"] = ExplosiveString(plan["schemaVersion"])
        with self.assertRaises(AnalysisContractError):
            verify(broken, request)
        recursive = deepcopy(plan)
        recursive["tables"][0]["title"] = recursive
        with self.assertRaises(AnalysisContractError):
            verify(recursive, request)

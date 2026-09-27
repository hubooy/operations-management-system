from copy import deepcopy
import json
from unittest import TestCase
from unittest.mock import patch

from . import screening_storage as store
from .contracts import AnalysisContractError, canonical, digest
from . import test_diagnostic_screening as fixtures


def result(*, records=6, wide=False):
    descriptor = fixtures.descriptor(rules=["erp_refund_present", "erp_gross_profit_negative"])
    prepared = fixtures.run(descriptor, [fixtures.row(descriptor, index) for index in range(records)])
    plan = {"families":[{"label":"中文"*1500 if wide else "合成来源"} for _ in range(23 if wide else 1)],
        "requestedCoverage":[], "selectionPolicy":"screen-selection-v1"}
    plan["planDigest"] = digest(plan)
    binding = {**fixtures.binding(), "algorithmVersion":prepared["algorithmVersion"]}
    authority = {"binding":binding, "executedTablesComplete":True, "completeSourceTraversalForExecutedTables":True,
        "tableCount":prepared["coverage"]["tableCount"], "partitionCount":len(prepared["partitions"]),
        "selectionPlanDigest":plan["planDigest"], "pureResultDigest":prepared["resultDigest"], "selectionPolicy":plan["selectionPolicy"]}
    value = {"schemaVersion":"business-diagnostic-screening-v1", "authority":authority,
        "bindingDigest":digest(binding), "planDigest":plan["planDigest"], "plan":plan, "prepared":prepared}
    value["resultDigest"] = digest(value)
    return value


def reseal(bundle):
    """Recompute byte hashes after corruption, never fix semantic coordinates."""
    manifest = json.loads(bundle["manifestJson"])
    root = store.INITIAL_CHAIN
    for group in manifest["groups"]:
        current = store.INITIAL_CHAIN
        for ordinal, sequence in enumerate(range(group["firstSequence"],group["lastSequence"]+1),1):
            page = bundle["pages"][sequence-1]
            page["payloadDigest"] = store.raw_digest(page["payloadJson"])
            current = store.chain(current,ordinal,page["payloadDigest"])
            root = store.chain(root,sequence,page["payloadDigest"])
        group["pagesDigest"] = current
    manifest["contentRootDigest"] = root
    bundle["manifestJson"] = canonical(manifest)
    bundle["storedBytes"] = len(bundle["bindingJson"].encode())+len(bundle["manifestJson"].encode())+sum(len(p["payloadJson"].encode()) for p in bundle["pages"])


class ScreeningStorageTests(TestCase):
    def test_complete_groups_exact_bytes_and_no_input_mutation(self):
        original = result(); before = deepcopy(original)
        bundle = store.materialize(original)
        manifest = store.validate(bundle)
        self.assertEqual(original,before)
        self.assertEqual(len(manifest["groups"]),3)
        self.assertEqual([p["sequence"] for p in bundle["pages"]],list(range(1,manifest["pageCount"]+1)))
        self.assertEqual(manifest["serviceResultDigest"],original["resultDigest"])
        self.assertEqual(bundle["storedBytes"],sum(len(p["payloadJson"].encode()) for p in bundle["pages"])+len(bundle["bindingJson"].encode())+len(bundle["manifestJson"].encode()))

    def test_wide_utf8_pages_keep_every_row_and_exact_next_offset(self):
        original = result(wide=True)
        bundle = store.materialize(original)
        coverage = [json.loads(p["payloadJson"]) for p in bundle["pages"] if p["kind"] == "coverage"]
        self.assertGreater(len(coverage),1)
        self.assertLess(coverage[0]["pagination"]["returned"],20)
        actual = [item["value"] for p in coverage for item in p["items"] if item["kind"] == "family"]
        self.assertEqual(actual,original["plan"]["families"])
        self.assertTrue(all(len(p["payloadJson"].encode()) <= 38000 for p in bundle["pages"]))

    def test_empty_candidates_each_keep_a_terminal_page(self):
        bundle = store.materialize(result(records=0))
        candidates = [p for p in bundle["pages"] if p["kind"] == "candidates"]
        self.assertEqual(len(candidates),2)
        self.assertTrue(all((p["offset"],p["returned"],p["total"],p["nextOffset"]) == (0,0,0,None) for p in candidates))

    def test_incomplete_or_reordered_pages_and_unaccounted_bytes_rejected(self):
        bundle = store.materialize(result(wide=True))
        for mutate in (lambda b:b["pages"].pop(), lambda b:b["pages"].reverse(),
                lambda b:b.update(storedBytes=b["storedBytes"]+1), lambda b:b["pages"][0].update(offset=1),
                lambda b:b["pages"][0].update(sequence=True), lambda b:b["pages"][0].update(returned=1.0)):
            changed = deepcopy(bundle); mutate(changed)
            with self.assertRaises(AnalysisContractError): store.validate(changed)

    def test_rehashed_partition_replacement_or_authority_binding_is_not_accepted(self):
        bundle = store.materialize(result())
        for target in ("partition", "binding", "pageDigest", "pagination", "authority"):
            changed = deepcopy(bundle)
            page = changed["pages"][-1]; payload = json.loads(page["payloadJson"])
            if target == "partition": payload["partition"]["matchedRows"] += 1; payload["partition"]["omittedRows"] += 1
            elif target == "binding": payload["authority"]["binding"]["reportId"] = "another"
            elif target == "pagination": payload["pagination"]["limit"] = 21
            elif target == "authority": payload["authority"]["executedTablesComplete"] = False
            payload["pageDigest"] = digest({k:v for k,v in payload.items() if k != "pageDigest"})
            if target == "pageDigest": payload["pageDigest"] = "0"*64
            page["payloadJson"] = canonical(payload); reseal(changed)
            with self.subTest(target=target), self.assertRaises(AnalysisContractError): store.validate(changed)

    def test_duplicate_coverage_partition_is_not_hidden_by_candidate_set(self):
        bundle = store.materialize(result())
        page = bundle["pages"][0]; payload = json.loads(page["payloadJson"])
        partition_items = [i for i in payload["items"] if i["kind"] == "partition"]
        partition_items[1]["value"] = deepcopy(partition_items[0]["value"])
        payload["pageDigest"] = digest({k:v for k,v in payload.items() if k != "pageDigest"})
        page["payloadJson"] = canonical(payload); reseal(bundle)
        with self.assertRaises(AnalysisContractError): store.validate(bundle)

    def test_capacity_refuses_whole_bundle_and_oversized_record(self):
        for key, maximum in (("MAX_PAGES",1),("MAX_RUN_BYTES",100),("MAX_MANIFEST_BYTES",100)):
            with patch.object(store,key,maximum), self.subTest(key=key), self.assertRaises(AnalysisContractError):
                store.materialize(result())
        with self.assertRaises(AnalysisContractError): store._page({},[{"text":"中"*20000}],0)

    def test_json_digest_version_and_numeric_lexemes_fail_closed(self):
        original = store.materialize(result())
        for mutate in (lambda b:b.update(bindingJson=b["bindingJson"]+" "),
                lambda b:b.update(manifestJson=b["manifestJson"].replace('"pageCount":3','"pageCount":3.0')),
                lambda b:b.update(manifestJson=b["manifestJson"].replace('screening-storage-v1','unknown'))):
            changed = deepcopy(original); mutate(changed)
            with self.assertRaises(AnalysisContractError): store.validate(changed)

    def test_chain_matches_raw_sql_compatible_sha_not_json_string_hash(self):
        previous, page = store.INITIAL_CHAIN, "a"*64
        expected = store.raw_digest(previous+":1:"+page)
        self.assertEqual(store.chain(previous,1,page),expected)
        self.assertNotEqual(expected,digest(previous+":1:"+page))

    def test_complete_large_result_does_not_inherit_single_object_node_limit(self):
        value = result(records=16)
        partition = value["prepared"]["partitions"][0]
        value["prepared"]["partitions"] = [{**deepcopy(partition),"partitionKey":digest(index),"tableKey":digest(["table",index])} for index in range(60)]
        table = value["prepared"]["coverage"]["tables"][0]
        value["prepared"]["coverage"]["tables"] = [{**deepcopy(table),"tableKey":digest(["table",index])} for index in range(60)]
        value["prepared"]["coverage"]["tableCount"] = value["authority"]["tableCount"] = value["authority"]["partitionCount"] = 60
        value["prepared"]["resultDigest"] = digest({k:v for k,v in value["prepared"].items() if k != "resultDigest"})
        value["authority"]["pureResultDigest"] = value["prepared"]["resultDigest"]
        value["resultDigest"] = digest({k:v for k,v in value.items() if k != "resultDigest"})
        bundle = store.materialize(value)
        self.assertEqual(len(store.validate(bundle)["groups"]),61)
        self.assertLess(bundle["storedBytes"],store.MAX_RUN_BYTES)

    def test_rehashed_pagination_float_and_bool_are_rejected(self):
        original = store.materialize(result())
        for key, val in (("limit",20.0),("offset",False),("returned",True),("total",6.0)):
            bundle = deepcopy(original); page = bundle["pages"][0]; payload = json.loads(page["payloadJson"])
            payload["pagination"][key] = val
            payload["pageDigest"] = digest({k:v for k,v in payload.items() if k != "pageDigest"})
            page["payloadJson"] = canonical(payload); reseal(bundle)
            with self.subTest(key=key), self.assertRaises(AnalysisContractError): store.validate(bundle)

    def test_rehashed_partition_counts_have_strict_integer_types(self):
        original = store.materialize(result(records=1))
        key = json.loads(original["manifestJson"])["groups"][1]["partitionKey"]
        for field in ("matchedRows", "retainedRows", "omittedRows"):
            for val in (True,1.0,None):
                bundle = deepcopy(original)
                for page in bundle["pages"]:
                    payload = json.loads(page["payloadJson"])
                    targets = ([i["value"] for i in payload["items"] if i["kind"] == "partition"]
                        if page["kind"] == "coverage" else [payload["partition"]])
                    for partition in targets:
                        if partition["partitionKey"] == key: partition[field] = val
                    payload["pageDigest"] = digest({k:v for k,v in payload.items() if k != "pageDigest"})
                    page["payloadJson"] = canonical(payload)
                reseal(bundle)
                with self.subTest(field=field,val=val), self.assertRaises(AnalysisContractError): store.validate(bundle)

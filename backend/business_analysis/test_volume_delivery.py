"""Pure receipt tests: synthetic data only, no ORM or services."""
from copy import deepcopy
import hashlib
import io
from unittest import TestCase

from . import volume_delivery as delivery, volume_files, volume_plan
from .budget import calculate
from .budget_offline import payload
from .contracts import AnalysisContractError, canonical, digest
from .report_files import Column, Table
from .test_budget import fixture as budget_fixture


BINDING = "b" * 64
EVIDENCE = "a" * 64
IDENTITY = {"binding_digest": BINDING, "attempt": 1, "draft": False}
BINDINGS = {**IDENTITY, "report_id": "report-delivery-test", "evidence_digest": EVIDENCE}


def resign(full):
    full["manifestDigest"] = digest({k: v for k, v in full.items() if k != "manifestDigest"})
    return full


def index_json(root, data):
    root["manifestFile"].update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                                chunkCount=(len(data) + delivery.CHUNK_BYTES - 1) // delivery.CHUNK_BYTES)
    return root


def synthetic_manifest(count=1):
    """Proof-shaped synthetic fixture, not a claim of stored file existence."""
    descriptors = [{"key": str(i), "title": "合成", "rowCount": 1, "columnCount": 1} for i in range(count)]
    plan = volume_plan.build({"schemaVersion": volume_plan.REQUEST_SCHEMA, "reportId": BINDINGS["report_id"],
        "evidenceDigest": EVIDENCE, "rendererVersion": 4, "tables": descriptors}, max_tables=1)
    volumes = []
    for v in plan["volumes"]:
        volumes.append({**v, "rowCount": 1, "offlineBudgetEnabled": False,
            "tables": [{**t, "sheet": "合成", "rowDigest": "c"*64, "precisionTextCells": 0} for t in v["tables"]],
            "files": {f: {"filename": f"{BINDINGS['report_id']}-volume-{v['volumeIndex']:03}-of-{count:03}.{f}",
                          "bytes": 1, "sha256": "d"*64} for f in ("html", "xlsx")}})
    return resign({"schemaVersion": "business-volume-files-v1", "status": "complete",
        **{k: plan[k] for k in ("reportId", "evidenceDigest", "rendererVersion", "planDigest", "sourceDescriptorDigest", "volumeCount", "sourceTableCount", "fragmentCount", "totalRows")},
        "byteCapacity": {"verified": True, "maxFileBytes": delivery.MAX_FILE_BYTES, "dynamicByteSplitting": False},
        "tables": [{**t, "rowDigest": "c"*64} for t in descriptors], "volumes": volumes})


class VolumeDeliveryTests(TestCase):
    def test_v6_opc_and_trusted_version_binding_are_not_inferred_from_manifest(self):
        import zipfile
        tables = [Table("one", "仅合成", "", (Column("n", "值", "integer"),), [[7]], 1)]
        request = volume_files.request_for(tables, report_id=BINDINGS["report_id"], evidence_digest=EVIDENCE, renderer_version=6)
        plan = volume_plan.build(request)
        outputs = [volume_files.VolumeStreams(io.BytesIO(), io.BytesIO())]
        full = volume_files.render(tables, outputs, report_id=BINDINGS["report_id"], evidence_digest=EVIDENCE,
            renderer_version=6, plan=plan, title="仅合成", metadata={})
        with zipfile.ZipFile(outputs[0].xlsx) as archive:
            self.assertIn(b'Extension="json" ContentType="application/json"', archive.read("[Content_Types].xml"))
        compact, raw = delivery.make(full, **IDENTITY, renderer_version=6)
        self.assertEqual(delivery.verify_full(compact, raw, **BINDINGS, renderer_version=6), full)
        for version in (4, 5, True, "6", 7):
            with self.subTest(version=version), self.assertRaises(AnalysisContractError):
                delivery.verify_full(compact, raw, **BINDINGS, renderer_version=version)
        changed = deepcopy(compact)
        changed["rendererVersion"] = 4
        with self.assertRaises(AnalysisContractError):
            delivery.verify_full(changed, raw, **BINDINGS)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tables = [Table("split", "中英<恶意>", "仅合成", (Column("value", "对象"),),
                        ([f"=值{i}"] for i in range(3)), 3),
                  Table("empty", "空表", "仅合成", (Column("value", "对象"),), iter(()), 0)]
        request = volume_files.request_for(tables, report_id=BINDINGS["report_id"], evidence_digest=EVIDENCE, renderer_version=4)
        cls.policy = {"max_tables": 1, "max_rows": 2}
        plan = volume_plan.build(request, **cls.policy)
        cls.outputs = [volume_files.VolumeStreams(io.BytesIO(), io.BytesIO()) for _ in plan["volumes"]]
        cls.full = volume_files.render(tables, cls.outputs, report_id=BINDINGS["report_id"], evidence_digest=EVIDENCE,
            renderer_version=4, plan=plan, title="合成多卷合同", metadata={}, **cls.policy)

    def test_actual_three_volumes_and_empty_table_roundtrip(self):
        root, data = delivery.make(self.full, **IDENTITY, **self.policy)
        verified = delivery.verify_full(root, data, **BINDINGS, **self.policy)
        self.assertEqual(verified, self.full)
        self.assertEqual(root["volumeCount"], 3)
        self.assertEqual([(f["volumeIndex"], f["format"]) for f in root["files"]],
                         [(i, f) for i in (1, 2, 3) for f in ("html", "xlsx")])
        for proof in root["files"]:
            stream = getattr(self.outputs[proof["volumeIndex"]-1], proof["format"])
            self.assertEqual(proof["bytes"], len(stream.getvalue()))
            self.assertEqual(proof["sha256"], hashlib.sha256(stream.getvalue()).hexdigest())
        self.assertEqual(data, canonical(self.full).encode())
        verified["tables"][0]["title"] = "changed"
        root["files"][0]["bytes"] = 1
        self.assertEqual(self.full["tables"][0]["title"], "中英<恶意>")

    def test_100_volumes_supported_and_101_rejected(self):
        full = synthetic_manifest(100)
        root, data = delivery.make(full, **IDENTITY, max_tables=1)
        self.assertLess(len(canonical(root).encode()), delivery.MAX_ROOT_BYTES)
        self.assertEqual(len(root["files"]), 200)
        delivery.verify_full(root, data, **BINDINGS, max_tables=1)
        root["volumeCount"] = 101
        with self.assertRaises(AnalysisContractError):
            delivery.validate(root, **IDENTITY)

    def test_compact_strict_types_fields_bindings_and_order(self):
        root, _ = delivery.make(self.full, **IDENTITY, **self.policy)
        mutations = [lambda r: r.update(rendererVersion=True), lambda r: r.update(rendererVersion=4.0),
            lambda r: r.update(attempt="1"), lambda r: r.update(draft=0), lambda r: r.update(extra=1),
            lambda r: r.update(bindingDigest="c"*64), lambda r: r.update(volumeCount=0),
            lambda r: r["files"].reverse(), lambda r: r["files"].pop(),
            lambda r: r["files"][0].update(chunkCount=True), lambda r: r["files"][0].update(bytes=0),
            lambda r: r["files"][0].update(sha256="A"*64), lambda r: r["files"][0].update(extra=0),
            lambda r: r["manifestFile"].update(volumeIndex=1), lambda r: r["manifestFile"].update(format="html")]
        for mutate in mutations:
            value = deepcopy(root); mutate(value)
            with self.subTest(value=value), self.assertRaises(AnalysisContractError):
                delivery.validate(value, **IDENTITY)
        for kwargs in ({**IDENTITY, "attempt": True}, {**IDENTITY, "attempt": 6}, {**IDENTITY, "draft": 1}, {**IDENTITY, "binding_digest": "x"}):
            with self.assertRaises(AnalysisContractError):
                delivery.validate(root, **kwargs)

    def test_chunk_ceil_and_file_json_total_caps(self):
        root, _ = delivery.make(synthetic_manifest(2), **IDENTITY, max_tables=1)
        for size in (1, delivery.CHUNK_BYTES, delivery.CHUNK_BYTES+1, delivery.MAX_FILE_BYTES):
            value = deepcopy(root)
            value["files"][0].update(bytes=size, chunkCount=(size+delivery.CHUNK_BYTES-1)//delivery.CHUNK_BYTES)
            delivery.validate(value, **IDENTITY)
            value["files"][0]["chunkCount"] += 1
            with self.assertRaises(AnalysisContractError):
                delivery.validate(value, **IDENTITY)
        for item, limit in (("files", delivery.MAX_FILE_BYTES), ("manifestFile", delivery.MAX_MANIFEST_BYTES)):
            value = deepcopy(root); proof = value[item][0] if item == "files" else value[item]
            proof.update(bytes=limit+1, chunkCount=(limit+delivery.CHUNK_BYTES)//delivery.CHUNK_BYTES)
            with self.assertRaises(AnalysisContractError):
                delivery.validate(value, **IDENTITY)
        # Exactly 1GiB includes the JSON artifact; a single additional byte fails.
        value = deepcopy(root)
        for proof in value["files"]:
            proof.update(bytes=delivery.MAX_FILE_BYTES, chunkCount=512)
        value["files"][-1]["bytes"] -= value["manifestFile"]["bytes"]
        value["files"][-1]["chunkCount"] = (value["files"][-1]["bytes"]+delivery.CHUNK_BYTES-1)//delivery.CHUNK_BYTES
        delivery.validate(value, **IDENTITY)
        value["files"][-1]["bytes"] += 1
        with self.assertRaises(AnalysisContractError):
            delivery.validate(value, **IDENTITY)

    def test_rehashed_full_tampering_cannot_replace_plan_or_proofs(self):
        mutations = [lambda f: f.update(extra=1), lambda f: f.update(status="partial"), lambda f: f.update(rendererVersion=3),
            lambda f: f.update(planDigest="c"*64), lambda f: f.update(sourceTableCount=True), lambda f: f.update(totalRows=4),
            lambda f: f["byteCapacity"].update(verified=1), lambda f: f["volumes"].reverse(),
            lambda f: f["volumes"][0]["tables"][0].update(rowOffset=1),
            lambda f: f["volumes"][0]["tables"][0].update(fragmentCount=3),
            lambda f: f["volumes"][0]["tables"][0].update(precisionTextCells=3),
            lambda f: f["volumes"][-1]["tables"][0].update(rowDigest="c"*64),
            lambda f: f["volumes"][0]["files"]["html"].update(filename="../escape.html"),
            lambda f: f["volumes"][1].update(offlineBudgetEnabled=True),
            lambda f: f["volumes"][0]["tables"].pop()]
        for mutate in mutations:
            full = deepcopy(self.full); mutate(full); resign(full)
            with self.subTest(mutate=mutate), self.assertRaises(AnalysisContractError):
                delivery.make(full, **IDENTITY, **self.policy)

    def test_full_stored_json_and_compact_cross_check(self):
        root, data = delivery.make(self.full, **IDENTITY, **self.policy)
        for kwargs in ({**BINDINGS, "report_id": "different"}, {**BINDINGS, "evidence_digest": "c"*64},
                       {**BINDINGS, "plan_digest": "c"*64}):
            with self.assertRaises(AnalysisContractError):
                delivery.verify_full(root, data, **kwargs, **self.policy)
        with self.assertRaises(AnalysisContractError):
            delivery.verify_full(root, data+b" ", **BINDINGS, **self.policy)
        root["files"][0]["sha256"] = "c"*64
        with self.assertRaises(AnalysisContractError):
            delivery.verify_full(root, data, **BINDINGS, **self.policy)

    def test_canonical_duplicate_keys_deep_invalid_and_oversized_json(self):
        root, data = delivery.make(self.full, **IDENTITY, **self.policy)
        bad_values = [b" "+data, b'{"schemaVersion":"duplicate",'+data[1:], b"["*1500+b"0"+b"]"*1500,
                      data.replace(b'"rendererVersion":4', b'"rendererVersion":4.0'), b'"\xff"']
        for bad in bad_values:
            compact = index_json(deepcopy(root), bad)
            with self.assertRaises(AnalysisContractError):
                delivery.verify_full(compact, bad, **BINDINGS, **self.policy)
        with self.assertRaises(AnalysisContractError):
            delivery.verify_full(root, b" "*(delivery.MAX_MANIFEST_BYTES+1), **BINDINGS, **self.policy)
        deep = []
        for _ in range(40):
            deep = [deep]
        for bad in (deep, {"payload": "中"*delivery.MAX_ROOT_BYTES}, {"payload": float("nan")}, {"payload": object()}):
            with self.assertRaises(AnalysisContractError):
                delivery.validate(bad, **IDENTITY)

    def test_budget_reserved_first_volume_roundtrip(self):
        p, bases = budget_fixture()
        budget = payload(calculate(p, bases), BINDINGS["report_id"]); budget["excelEnabled"] = True
        tables = [Table("budget", "预算合成来源", "", (Column("n", "数"),), [[1], [2], [3]], 3)]
        policy = {"max_tables": 4, "max_rows": 2}
        request = volume_files.request_for(tables, report_id=BINDINGS["report_id"], evidence_digest=EVIDENCE, renderer_version=4)
        plan = volume_plan.build(request, native_budget_sheets=3, **policy)
        outputs = [volume_files.VolumeStreams(io.BytesIO(), io.BytesIO()) for _ in plan["volumes"]]
        full = volume_files.render(tables, outputs, report_id=BINDINGS["report_id"], evidence_digest=EVIDENCE, renderer_version=4,
            plan=plan, title="合成预算", metadata={}, offline_budget=budget, excel_budget=budget, **policy)
        root, data = delivery.make(full, **IDENTITY, **policy)
        self.assertEqual(delivery.verify_full(root, data, **BINDINGS, **policy)["budgetPlanDigest"], budget["planDigest"])
        full["volumes"][0]["budgetCalculator"]["planDigest"] = "c"*64; resign(full)
        with self.assertRaises(AnalysisContractError):
            delivery.make(full, **IDENTITY, **policy)

    def test_default_policy_rebuilt_without_separate_plan_digest(self):
        full = synthetic_manifest()
        # Recompute the fixture with default capacity: same fragments, distinct plan binding.
        request = {"schemaVersion": volume_plan.REQUEST_SCHEMA, "reportId": BINDINGS["report_id"],
                   "evidenceDigest": EVIDENCE, "rendererVersion": 4,
                   "tables": [{k: t[k] for k in delivery.DESCRIPTOR_FIELDS} for t in full["tables"]]}
        full["planDigest"] = volume_plan.build(request)["planDigest"]; resign(full)
        root, data = delivery.make(full, **IDENTITY)
        self.assertEqual(delivery.verify_full(root, data, **BINDINGS), full)

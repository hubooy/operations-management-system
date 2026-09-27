"""Pure ledger projections: no database, provider or production reader access."""
from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from business_analysis import evidence_v2
from . import business_evidence_receipts as receipts
from .policy import AiError, canonical, digest


class BusinessEvidenceReceiptTests(TestCase):
    def setUp(self):
        self.sources = [{"key": f"source-{i:02}", "domain": "sales", "query": {
            "platform": "京东", "shop": f"店铺{i:02}", "channel": f"精确渠道{i:02}",
            "startDate": "2026-08-01", "endDate": "2026-08-31", "window": "current"}} for i in range(48)]
        built = evidence_v2.build_catalog(self.sources)
        self.header = built["header"]
        self.sources = [{key: entry[key] for key in ("key", "domain", "query")} for entry in built["entries"]]
        self.row = SimpleNamespace(id="evidence-1", version=20, status="sealed", owner_email="a@example.invalid",
            scope_json="null", plan_json=canonical(self.header), state_json=canonical({"sealedDigest": "d"*64}))
        self.job = SimpleNamespace(id="job-1", owner_email=self.row.owner_email, scope_json="null")
        self.snapshot = {"executionProfile": "business-agent-reference-v2", "evidenceProtocol": "reference-v2",
            "evidenceRunId": self.row.id, "evidenceVersion": self.row.version, "evidencePlanDigest": digest(self.row.plan_json),
            "catalogDigest": self.header["catalogDigest"], "sealedDigest": "d"*64, "sourceCount": 48}
        self.dispatches, self.results = [], {}
        self.dispatched_query = MagicMock()
        self.dispatched_query.order_by.return_value = self.dispatched_query
        self.dispatched_query.values.return_value = self.dispatched_query
        self.dispatched_query.annotate.return_value = self.dispatched_query
        def dispatch_projection(value):
            return [{**row, "arguments_text": row["arguments_json"][:receipts.MAX_ARGUMENT_BYTES+1],
                "argument_bytes": len(row["arguments_json"].encode())} for row in self.dispatches[value]]
        self.dispatched_query.__getitem__.side_effect = dispatch_projection
        self.dispatch_manager = MagicMock()
        self.dispatch_manager.filter.return_value = self.dispatched_query
        self.result_manager = MagicMock()
        def result_query(**params):
            query = MagicMock()
            record = self.results.get(params["tool_dispatch_id"])
            projected = None if record is None else {**record, "result_text": record["result_json"][:receipts.MAX_RESULT_BYTES+1],
                "result_bytes": len(record["result_json"].encode())}
            query.annotate.return_value.values.return_value.first.return_value = projected
            return query
        self.result_manager.filter.side_effect = result_query
        self.patches = [
            patch.object(receipts.m.AiAgentToolDispatches, "objects", self.dispatch_manager),
            patch.object(receipts.m.AiAgentToolResults, "objects", self.result_manager),
            patch.object(receipts, "_trusted", return_value=(self.row, self.header, self.sources)),
        ]
        for context in self.patches:
            context.start()
            self.addCleanup(context.stop)

    def add(self, offset=0, *, ok=True, state="succeeded", name=receipts.DIRECTORY_TOOL, data=None):
        number = len(self.dispatches)+1
        args = {"runId": self.row.id, "offset": offset}
        if data is None:
            data = evidence_v2.directory_page(self.sources, run_id=self.row.id, evidence_version=self.row.version, offset=offset, limit=20)
        result = {"toolName": name, "ok": ok, "auditStatus": "recorded", "data": data}
        dispatched = {"id": f"dispatch-{number}", "job_id": self.job.id, "provider_dispatch__job_id": self.job.id,
            "tool_call_ordinal": number, "tool_name": name, "state": state,
            "arguments_json": canonical(args), "arguments_digest": digest(args)}
        receipt = {"tool_dispatch_id": dispatched["id"], "result_json": canonical(result), "result_digest": digest(result)}
        self.dispatches.append(dispatched)
        self.results[dispatched["id"]] = receipt
        return dispatched, receipt

    def complete(self):
        offset = 0
        while offset < len(self.sources):
            page = evidence_v2.directory_page(self.sources, run_id=self.row.id, evidence_version=self.row.version, offset=offset, limit=20)
            self.add(offset, data=page)
            offset += page["returned"]

    def changed_result(self, receipt, change):
        import json
        result = json.loads(receipt["result_json"])
        change(result)
        receipt.update(result_json=canonical(result), result_digest=digest(result))

    def reject(self):
        with self.assertRaises(AiError):
            receipts.validate_directory_complete(self.job, self.snapshot)

    def test_empty_partial_and_complete_are_per_job_bounded(self):
        self.assertEqual(receipts.directory_progress(self.job, self.snapshot), {
            "nextOffset": 0, "complete": False, "pages": 0, "catalogDigest": self.header["catalogDigest"]})
        self.reject()
        self.add(0)
        self.assertEqual(receipts.directory_progress(self.job, self.snapshot)["nextOffset"], 20)
        self.reject()
        self.add(20)
        self.add(40)
        self.assertEqual(receipts.validate_directory_complete(self.job, self.snapshot)["pages"], 3)
        self.assertIsNone(receipts.directory_progress(self.job, self.snapshot)["nextOffset"])
        self.dispatch_manager.filter.assert_called_with(job_id="job-1")
        self.dispatched_query.__getitem__.assert_called_with(slice(None, 41, None))

    def test_failed_receipt_does_not_count_and_explicit_retry_can_complete(self):
        self.add(0, ok=False)
        self.assertEqual(receipts.directory_progress(self.job, self.snapshot)["pages"], 0)
        self.complete()
        self.assertTrue(receipts.validate_directory_complete(self.job, self.snapshot)["complete"])

    def test_unknown_and_calling_never_authorize_replay_even_with_result(self):
        dispatched, _ = self.add(0)
        for state in ("unknown", "calling"):
            dispatched["state"] = state
            with self.assertRaises(AiError) as caught:
                receipts.directory_progress(self.job, self.snapshot)
            self.assertEqual(caught.exception.code, "tool_dispatch_unknown")

    def test_missing_duplicate_and_reordered_directory_pages_fail(self):
        for offsets in ([0, 40], [0, 0, 20, 40], [20, 0, 40], [0, 20, 40, 0]):
            self.dispatches.clear(); self.results.clear()
            for offset in offsets:
                self.add(offset)
            self.reject()

    def test_dispatch_and_result_digests_checked(self):
        dispatched, receipt = self.add()
        original = dispatched["arguments_digest"]
        dispatched["arguments_digest"] = "0"*64
        self.reject()
        dispatched["arguments_digest"] = original
        receipt["result_digest"] = "0"*64
        self.reject()

    def test_rehashed_forged_page_cannot_replace_trusted_source_identity(self):
        _, receipt = self.add()
        def forge(result):
            page = result["data"]
            page["items"][0]["query"]["shop"] = "其他店铺"
            page["items"][0]["queryDigest"] = digest(page["items"][0]["query"])
            page["pageDigest"] = digest({k: v for k, v in page.items() if k != "pageDigest"})
        self.changed_result(receipt, forge)
        self.reject()

    def test_all_page_binding_fields_are_rebuilt(self):
        for key, value in (("runId", "other"), ("evidenceVersion", 21), ("planDigest", "e"*64),
                ("catalogDigest", "e"*64), ("total", 47), ("nextOffset", 40)):
            self.dispatches.clear(); self.results.clear()
            _, receipt = self.add()
            self.changed_result(receipt, lambda result: result["data"].update({key: value}))
            self.reject()

    def test_cross_job_provider_and_receipt_are_not_coverage(self):
        dispatched, receipt = self.add()
        for key in ("job_id", "provider_dispatch__job_id"):
            dispatched[key] = "sibling-job"
            self.reject()
            dispatched[key] = self.job.id
        receipt["tool_dispatch_id"] = "sibling-dispatch"
        self.reject()

    def test_ordinal_gaps_and_scan_overflow_fail(self):
        dispatched, _ = self.add()
        dispatched["tool_call_ordinal"] = 2
        self.reject()
        self.dispatches[:] = [deepcopy(dispatched) for _ in range(41)]
        self.reject()

    def test_unaudited_wrong_tool_and_success_without_result_fail(self):
        _, receipt = self.add()
        self.changed_result(receipt, lambda result: result.update(auditStatus="unavailable"))
        self.reject()
        self.changed_result(receipt, lambda result: result.update(auditStatus="recorded", toolName="other"))
        self.reject()
        self.results.clear()
        self.reject()

    def test_analysis_before_complete_rejected_after_complete_accepted(self):
        self.add(0, name="get_business_analysis_table_v2", data={"rows": []})
        self.reject()
        self.dispatches.clear(); self.results.clear()
        self.complete()
        self.add(0, name="get_business_analysis_table_v2", data={"rows": []})
        self.assertTrue(receipts.validate_directory_complete(self.job, self.snapshot)["complete"])

    def test_request_has_exact_run_offset_and_limit(self):
        for key, value in (("runId", "other"), ("offset", True), ("limit", 10), ("extra", "x")):
            self.dispatches.clear(); self.results.clear()
            dispatched, _ = self.add()
            args = {"runId": self.row.id, "offset": 0, key: value}
            dispatched.update(arguments_json=canonical(args), arguments_digest=digest(args))
            self.reject()

    def test_first_offset_can_be_omitted_but_limit_is_adapter_owned(self):
        dispatched, _ = self.add()
        args = {"runId": self.row.id}
        dispatched.update(arguments_json=canonical(args), arguments_digest=digest(args))
        self.assertEqual(receipts.directory_progress(self.job, self.snapshot)["nextOffset"], 20)

    def test_oversized_or_invalid_receipts_fail_closed(self):
        _, receipt = self.add()
        for raw in ("{"+"x"*300000, "[]", "{broken"):
            receipt.update(result_json=raw, result_digest=digest(raw))
            self.reject()

    def test_deep_untrusted_shapes_and_extra_fields_raise_ai_error(self):
        _, receipt = self.add()
        for depth in (200, 1500):
            raw = ('{"toolName":"'+receipts.DIRECTORY_TOOL+'","ok":true,"auditStatus":"recorded","data":'
                +'['*depth+'0'+']'*depth+'}')
            receipt.update(result_json=raw, result_digest=digest(raw))
            self.reject()
        self.dispatches.clear(); self.results.clear()
        _, receipt = self.add()
        self.changed_result(receipt, lambda value: value["data"].update(extra="x"))
        self.reject()

    def test_byte_limited_pages_advance_by_actual_returned_count(self):
        sources = [{"key": f"market-{i:02}", "domain": "market", "query": {
            "platform": "京东", "category": "类"*200, "scope": "范"*200,
            "rankingDimension": "SKU", "priceBandFilter": f"{i:02}"+"价"*198,
            "startDate": "2026-08-01", "endDate": "2026-08-31", "window": "current"}} for i in range(48)]
        built = evidence_v2.build_catalog(sources)
        self.header = built["header"]
        self.sources = [{key: entry[key] for key in ("key", "domain", "query")} for entry in built["entries"]]
        receipts._trusted.return_value = (self.row, self.header, self.sources)
        first = evidence_v2.directory_page(self.sources, run_id=self.row.id, evidence_version=self.row.version, limit=20)
        self.assertLess(first["returned"], 20)
        self.complete()
        self.assertTrue(receipts.validate_directory_complete(self.job, self.snapshot)["complete"])

    def test_trusted_binding_checks_owner_seal_profile_and_all_digests(self):
        # Exercise the real trust loader with only ORM/store boundaries replaced.
        self.patches[-1].stop()
        with patch.object(receipts.m.AiBusinessEvidenceRun.objects, "filter") as query, \
                patch.object(receipts.store, "is_v2", return_value=True), \
                patch.object(receipts.store, "verify_seal") as verify, \
                patch.object(receipts.store, "catalog", return_value=self.sources):
            query.return_value.first.return_value = self.row
            self.assertEqual(receipts.directory_progress(self.job, self.snapshot)["pages"], 0)
            verify.assert_called_once_with(self.row)
            for key in ("evidenceRunId", "evidenceVersion", "evidencePlanDigest", "catalogDigest", "sealedDigest", "sourceCount", "executionProfile", "evidenceProtocol"):
                changed = {**self.snapshot, key: "wrong"}
                with self.assertRaises(AiError):
                    receipts.directory_progress(self.job, changed)
            for key, value in (("owner_email", "other@example.invalid"), ("scope_json", "{}"), ("status", "collecting")):
                old = getattr(self.row, key)
                setattr(self.row, key, value)
                self.reject()
                setattr(self.row, key, old)

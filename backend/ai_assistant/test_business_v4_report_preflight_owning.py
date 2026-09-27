"""Test-only owning authorization preflight stays blocked after HMAC check."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from business_analysis import (evidence_seal_v4, period_bound_plan_v1)
from business_analysis.contracts import canonical
from business_analysis.test_report_v4_authorization_preflight_v1 import (
    fixture)

from . import business_v4_report_preflight_owning as owning
from .policy import AiError


@override_settings(DJANGO_PROCESS_ROLE="development",
    DJANGO_ENVIRONMENT="test")
class V4ReportPreflightOwningTests(SimpleTestCase):
    def test_existing_v4_owner_compares_body_to_parent_raw_plan_digest(self):
        _, _, plan, verified, raw = fixture()
        parent = SimpleNamespace(id=verified["runId"],
            version=verified["evidenceVersion"],
            plan_json=canonical(plan),
            plan_digest=hashlib.sha256(canonical(plan).encode()).hexdigest())
        sources = [SimpleNamespace(source_key=row["sourceKey"],
            source_ref=row["sourceRef"],
            source_revision=row["sourceRevision"])
            for row in verified["sourceRefs"]]
        seal = SimpleNamespace(body_json=raw,
            body_digest=verified["sealedDigest"],
            body_mac="b"*64,key_id="a"*16)
        query = Mock()
        query.first.return_value = seal
        with patch.object(owning.source_owner.v4_owner,
                "verify_seal",return_value=verified), \
                patch.object(owning.source_owner.v4_owner,"_directory",
                    return_value=({"email":"admin@example.test"},
                        parent,sources,"e"*64)), \
                patch.object(owning.source_owner.m.AiBusinessV4Seal.objects,
                    "filter",return_value=query):
            value = owning.source_owner._v4(parent.id,
                SimpleNamespace(email="admin@example.test",scope=None))
        self.assertEqual(value[5]["planDigest"], parent.plan_digest)
        self.assertNotEqual(value[5]["planDigest"], plan["planDigest"])

    def contexts(self):
        report_proof, link, plan, verified, raw = fixture()
        report, link = deepcopy(report_proof), deepcopy(link)
        snapshot_raw, input_raw = "{}", "{}"
        report["reportSnapshotDigest"] = link[
            "reportSnapshotDigest"] = hashlib.sha256(
                snapshot_raw.encode()).hexdigest()
        report["workflowInputDigest"] = link[
            "workflowInputDigest"] = hashlib.sha256(
                input_raw.encode()).hexdigest()
        actual = SimpleNamespace(id=report["reportId"],
            owner_email=report["ownerEmail"],
            snapshot_json=snapshot_raw,
            workflow=SimpleNamespace(input_json=input_raw))
        evidence = SimpleNamespace(id=report["v2EvidenceRunId"],
            version=report["v2EvidenceVersion"])
        snapshot = {"sealedDigest": report["v2SealedDigest"]}
        scope = {"platform": "京东", "shop": report["shop"],
            **report["originalPeriod"]}
        report_context = (actual, snapshot, evidence, {},
            plan["analysisRequest"], scope)
        parent = SimpleNamespace(id=verified["runId"],
            version=verified["evidenceVersion"])
        seal_state = {"body_digest": verified["sealedDigest"]}
        v4_context = ({"email": report["ownerEmail"]}, parent,
            [], "directory", verified, evidence_seal_v4.read(raw),
            plan, period_bound_plan_v1.prepare_candidate(plan),
            seal_state)
        row = {"owner_email": report["ownerEmail"],
            "v4_run_id": verified["runId"],
            "v2_run_id": evidence.id,
            "v2_sealed_digest": snapshot["sealedDigest"],
            "v4_sealed_digest": verified["sealedDigest"],
            "report_snapshot_digest": report["reportSnapshotDigest"],
            "workflow_input_digest": report["workflowInputDigest"]}
        principal = SimpleNamespace(email=report["ownerEmail"],scope=None)
        return report_context,v4_context,row,link,principal

    def test_current_sql_and_real_hmac_owner_checks_still_block_report_use(self):
        report,v4,row,link,principal = self.contexts()
        with patch.object(owning.source_owner,"_report",
                return_value=report) as bound, \
                patch.object(owning.source_owner,"_v4",
                    return_value=v4) as sealed, \
                patch.object(owning,"_link",return_value=(row,link)) as sql:
            value = owning.inspect_candidate(report[0].id,v4[1].id,
                principal,enabled=True)
        self.assertEqual((bound.call_count,sealed.call_count,sql.call_count),
            (2,2,2))
        self.assertEqual(value["status"],
            "blocked_legacy_v4_report_authority")
        self.assertTrue(value["currentV4ApplicationHmacRechecked"])
        self.assertFalse(value["v4RowsReferencableIn13Tables"])
        self.assertFalse(value["v4VolumesPublishable"])
        self.assertFalse(value["downloadSupported"])

    def test_unknown_finance_mapping_or_final_link_drift_refuses(self):
        report,v4,row,link,principal = self.contexts()
        changed_v4 = list(v4)
        changed_v4[5] = deepcopy(v4[5])
        finance = next(item for item in changed_v4[5]["sources"]
            if item["domain"] == "finance")
        finance["scope"] = {"scope_key":"business",
            "scope_type":"business", "scope_name":"集团",
            "group_name":""}
        with patch.object(owning.source_owner,"_report",return_value=report), \
                patch.object(owning.source_owner,"_v4",
                    return_value=tuple(changed_v4)), \
                patch.object(owning,"_link",return_value=(row,link)), \
                self.assertRaises(AiError):
            owning.inspect_candidate(report[0].id,v4[1].id,
                principal,enabled=True)
        drift = {**row,"v4_sealed_digest":"0"*64}
        with patch.object(owning.source_owner,"_report",return_value=report), \
                patch.object(owning.source_owner,"_v4",return_value=v4), \
                patch.object(owning,"_link",side_effect=[(row,link),
                    (drift,link)]), self.assertRaises(AiError):
            owning.inspect_candidate(report[0].id,v4[1].id,
                principal,enabled=True)

    def test_default_and_non_test_roles_refuse_before_source_read(self):
        report,v4,row,link,principal = self.contexts()
        with patch.object(owning.source_owner,"_report") as source:
            with self.assertRaises(AiError):
                owning.inspect_candidate(report[0].id,v4[1].id,principal)
            source.assert_not_called()
        with override_settings(DJANGO_ENVIRONMENT="production"), \
                self.assertRaises(AiError):
            owning.inspect_candidate(report[0].id,v4[1].id,principal,
                enabled=True)

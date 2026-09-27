"""Pure orchestration plus actual sealed/published owning admission fixtures.

The catalog fixture was exported from the real 50-entry TypeScript registry.
Only its network delivery is mocked; PostgreSQL tests use actual permissions,
model rows, immutable reports, packages, budget resolution and frame measures.
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

from django import test as djtest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from access_control.models import AppUser

from . import business_screening_admission as service, business_screening_runtime_contract as contract
from . import business_screening_tools as tools, business_screening_packages as packages
from . import business_screening_store as store, business_diagnostic_screening as screening
from . import workflows, transport, provider, models as m
from . import test_business_screening_tools as fixtures, test_business_screening_preflight as pure_fixtures
from .business_sealed import Reader
from .policy import AiError, canonical, digest, mutation


def catalog():
    saved = json.loads((Path(__file__).parent/"fixtures"/"business_screening_catalog.json").read_text(encoding="utf-8"))
    assert saved["registryCount"] == 50 and saved["surface"] == contract.SURFACE
    assert digest(saved["entries"]) == saved["sha256"]
    return saved["entries"]


class AdmissionPureTests(unittest.TestCase):
    def test_real_ts_catalog_shape_order_and_hash_not_only_name_set(self):
        entries = catalog()
        flow = SimpleNamespace(allowed_tools_json=canonical([e["name"] for e in entries]),tool_policy_digest=digest(entries))
        self.assertEqual(service._catalog(entries,flow),entries)
        for bad in ([],entries[:2],entries+[entries[0]],list(reversed(entries)),[{},*entries[1:]]):
            with self.assertRaises(AiError):service._catalog(bad,flow)
        for path,value in ((["inputSchema","additionalProperties"],True),(["execution","maxCallsPerRequest"],8.0),
                (["inputSchema","properties","offset","maximum"],10000),(["name"],[]),(["schemaVersion"],"unknown")):
            bad=deepcopy(entries);item=bad[0]
            for key in path[:-1]:item=item[key]
            item[path[-1]]=value
            changed=SimpleNamespace(allowed_tools_json=canonical([e["name"] for e in bad]),tool_policy_digest=digest(bad))
            with self.assertRaises(AiError):service._catalog(bad,changed)

    def test_json_alias_raw_mutation_and_size_cannot_become_internal_permission(self):
        for value in ({},"{}",SimpleNamespace()):
            with self.assertRaises(AiError):service.revalidate(value,None)
        with self.assertRaises(AiError):service.PreparedAdmission(None,"{}",{}, {})
        value=service.PreparedAdmission(service._TOKEN,"{}",{"id":"one"},{"nested":{"value":1}})
        value.proof["nested"]["value"]=2
        self.assertEqual(value.proof["nested"]["value"],1)
        with self.assertRaises(AttributeError):value._proof_json="{}"
        object.__setattr__(value,"_proof_json","{}")
        with self.assertRaises(AiError):_ = value.proof
        with self.assertRaises(AiError):service.PreparedAdmission(service._TOKEN,"{}",{}, {"text":"x"*32768})

    def test_prepare_refuses_outer_transaction_before_any_read_or_network(self):
        with (patch.object(service,"connection",SimpleNamespace(in_atomic_block=True)),patch.object(service,"_current") as load,
                patch.object(transport,"catalog") as network):
            with self.assertRaises(AiError):service.prepare(None,None)
        load.assert_not_called();network.assert_not_called()

    def test_orchestration_uses_complete_real_frames_and_rechecks_after_measurement(self):
        role_pages,reference,budgets=pure_fixtures.fixture(already_proposed=True,with_budget=True)
        model=pure_fixtures.model();entries=catalog()
        flow=SimpleNamespace(input_json=canonical(reference["workflowInput"]),allowed_tools_json=canonical([e["name"] for e in entries]),tool_policy_digest=digest(entries))
        actual=SimpleNamespace(id=reference["workflowInput"]["reportId"],snapshot_json="{}",workflow=flow)
        fixed={"reportId":actual.id}
        ready=SimpleNamespace(packages=object())
        description={"reference":reference["screeningReference"],"roles":{r:{"packageDigest":d} for r,d in reference["packageDigests"].items()}}
        def pages(_,role,principal):
            return ({p["pagination"]["offset"]:p for p in role_pages[role]},
                {p["pagination"]["offset"]:{"budget":p} for p in budgets})
        with (patch.object(service,"connection",SimpleNamespace(in_atomic_block=False)),
                patch.object(service,"_current",return_value=(actual,reference["workflowInput"],model,"",fixed)) as reload,
                patch.object(transport,"catalog",return_value=entries),patch.object(tools,"prepare_for_report",return_value=ready),
                patch.object(tools,"expected_pages",side_effect=pages) as expected,
                patch.object(packages,"describe",return_value=description),patch.object(provider,"turn") as paid):
            value=service.prepare(actual,None)
            self.assertTrue(value.proof["capacityVerified"])
            self.assertFalse(value.proof["runtimeAdmissionGranted"])
            self.assertEqual(len(value.proof["measurements"]),6)
            self.assertEqual(expected.call_count,5);self.assertEqual(reload.call_count,2);paid.assert_not_called()
            original=service.preflight.measure
            def no_fit(*args,**kwargs):
                result=original(*args,**kwargs);result["fits"]=False;result["nodes"][0]["failures"]=["tool_limit_exceeded"]
                return result
            with patch.object(service.preflight,"measure",side_effect=no_fit),self.assertRaises(AiError) as error:service.prepare(actual,None)
            self.assertEqual(error.exception.code,"tool_limit_exceeded")
            with patch.object(service,"_current",side_effect=[(actual,reference["workflowInput"],model,"",fixed),AiError("late revoke")]),self.assertRaises(AiError):service.prepare(actual,None)


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",DJANGO_ENVIRONMENT="test")
class ScreeningAdmissionTests(djtest.TransactionTestCase):
    user=fixtures.ScreeningToolsTests.user
    call=fixtures.ScreeningToolsTests.call
    collect_body=fixtures.ScreeningToolsTests.collect_body
    bundle=fixtures.ScreeningToolsTests.bundle
    input_for=fixtures.ScreeningToolsTests.input_for
    insert=fixtures.ScreeningToolsTests.insert
    seed=fixtures.ScreeningToolsTests.seed
    setUp=fixtures.ScreeningToolsTests.setUp
    screening_bundle=fixtures.ScreeningToolsTests.screening_bundle
    insert_screening=fixtures.ScreeningToolsTests.insert_screening

    def ready(self,*,mapped=False,budget=False,publish=True,flow_changes=None,entries=None):
        entries=catalog() if entries is None else entries
        # This is a new fixture model version, explicitly pinned by the flow.
        with mutation(self.admin):
            m.AiModels.objects.filter(pk=self.model.pk).update(max_tool_rounds=20,max_total_tool_calls=40)
        self.model.refresh_from_db()
        changes={"dry_run":0,"model_id":self.model.id,"model_version":self.model.version,
            "allowed_tools_json":canonical([e["name"] for e in entries]),"tool_policy_digest":digest(entries)}
        changes.update(flow_changes or {})
        bundle=self.screening_bundle(mapped=mapped,budget=budget)
        with mutation(self.admin):report=self.insert_screening(bundle,flow_changes=changes)
        if publish:store.publish(screening.prepare_for_report(report.id,self.admin),self.admin)
        return report

    def test_four_real_mapping_budget_combinations_measure_without_writes_or_dispatch(self):
        for mapped,budget in ((False,False),(True,False),(False,True),(True,True)):
            report=self.ready(mapped=mapped,budget=budget)
            with (patch.object(transport,"catalog",return_value=catalog()) as network,
                    patch.object(provider,"turn",side_effect=AssertionError("no provider")),
                    patch.object(transport,"execute_tool",side_effect=AssertionError("no tool dispatch")),CaptureQueriesContext(connection) as queries):
                prepared=service.prepare(report,self.admin)
            network.assert_called_once_with(self.admin,contract.SURFACE)
            proof=prepared.proof
            self.assertTrue(proof["capacityVerified"]);self.assertFalse(proof["modelDispatched"])
            self.assertLessEqual(len(canonical(proof).encode()),32768)
            self.assertEqual(set(proof["packageDigests"]),set(contract.ROLES))
            self.assertNotIn("api_key",canonical(proof));self.assertNotIn(self.model.base_url,canonical(proof))
            self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT ","UPDATE ","DELETE ")) for q in queries))
            self.assertEqual(m.AiAgentJobs.objects.count(),0)
            with (patch.object(transport,"catalog",side_effect=AssertionError("no network recheck")),
                    patch.object(Reader,"pages",side_effect=AssertionError("no facts recheck")),mutation(self.admin)):
                self.assertEqual(service.revalidate(prepared,self.admin),proof)

    def test_missing_publication_dry_run_and_wrong_catalog_never_dispatch(self):
        for changes,publish,entries in (({},False,catalog()),({"dry_run":1},True,catalog()),({},True,list(reversed(catalog())))):
            report=self.ready(flow_changes=changes,publish=publish)
            with patch.object(transport,"catalog",return_value=entries),patch.object(provider,"turn") as paid,self.assertRaises(AiError):
                service.prepare(report,self.admin)
            paid.assert_not_called()
        report=self.ready()
        with transaction.atomic(),patch.object(transport,"catalog") as network,self.assertRaises(AiError):service.prepare(report,self.admin)
        network.assert_not_called()

    def test_actual_model_limit_and_version_changes_cannot_reuse_proof(self):
        report=self.ready()
        with patch.object(transport,"catalog",return_value=catalog()):prepared=service.prepare(report,self.admin)
        with mutation(self.admin):m.AiModels.objects.filter(pk=self.model.pk).update(version=self.model.version+1)
        with self.assertRaises(AiError) as error:service.revalidate(prepared,self.admin)
        self.assertEqual(error.exception.code,"model_version_changed")
        report=self.ready()
        with mutation(self.admin):m.AiModels.objects.filter(pk=self.model.pk).update(max_total_tool_calls=1,max_tool_rounds=1)
        with patch.object(transport,"catalog",return_value=catalog()),self.assertRaises(AiError) as error:service.prepare(report,self.admin)
        self.assertEqual(error.exception.code,"tool_limit_exceeded")

    def test_permission_changes_and_late_revocation_drop_admission(self):
        report=self.ready()
        with patch.object(transport,"catalog",return_value=catalog()):prepared=service.prepare(report,self.admin)
        other=self.user("admission-other@example.invalid","admin",None)
        with self.assertRaises(AiError):service.revalidate(prepared,other)
        original=service.preflight.measure
        def revoke(*args,**kwargs):
            result=original(*args,**kwargs)
            AppUser.objects.filter(email=self.admin.email).update(role_id="viewer")
            return result
        with patch.object(transport,"catalog",return_value=catalog()),patch.object(service.preflight,"measure",side_effect=revoke),self.assertRaises(AiError):
            service.prepare(report,self.admin)
        self.assertEqual(m.AiAgentJobs.objects.count(),0)

    def test_late_fixed_guidance_and_model_capability_changes_refuse_reuse(self):
        report=self.ready()
        with patch.object(transport,"catalog",return_value=catalog()):prepared=service.prepare(report,self.admin)
        with mutation(self.admin):
            m.AiExecutionGuidance.objects.create(entity_id=report.workflow_id,snapshot_json=canonical({"prompt":"新固定指导","guidance":{},"skills":{}}))
        with patch.object(transport,"catalog",side_effect=AssertionError("recheck must stay local")),self.assertRaises(AiError):
            service.revalidate(prepared,self.admin)
        with patch.object(transport,"catalog",return_value=catalog()):prepared=service.prepare(report,self.admin)
        with mutation(self.admin):m.AiModels.objects.filter(pk=self.model.pk).update(max_tokens=self.model.max_tokens+1)
        with self.assertRaises(AiError):service.revalidate(prepared,self.admin)

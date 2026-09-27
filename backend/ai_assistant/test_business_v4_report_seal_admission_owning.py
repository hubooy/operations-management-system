"""Test-only owning orchestration never upgrades a legacy v4 seal."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock,patch

from django.test import SimpleTestCase,override_settings

from business_analysis import evidence_seal_v4,period_bound_plan_v1
from business_analysis.test_v4_report_seal_admission_v2 import fixture

from . import business_v4_report_seal_admission_owning as owning
from .policy import AiError


@override_settings(DJANGO_ENVIRONMENT="test",
    DJANGO_PROCESS_ROLE="development")
class V4ReportSealAdmissionOwningTests(SimpleTestCase):
    def contexts(self):
        plan,verified,raw,receipts=fixture()
        body=evidence_seal_v4.read(raw)
        parent=SimpleNamespace(id=verified["runId"],
            version=verified["evidenceVersion"])
        sources=[]
        for index,entry in enumerate(plan["sourcePlans"]):
            ref=verified["sourceRefs"][index]
            sources.append(SimpleNamespace(id="source-"+str(index),
                source_key=entry["sourceKey"],version=2,
                query_digest=entry["queryDigest"],
                source_identity_digest=entry["sourceIdentityDigest"],
                source_ref=ref["sourceRef"],
                source_revision=ref["sourceRevision"],
                checkpoint_json="{}"))
        actor={"email":"admin@example.test"}
        seal_state={"body_digest":verified["sealedDigest"],
            "body_json":raw,"body_mac":"b"*64,"key_id":"a"*16}
        context=(actor,parent,sources,"d"*64,verified,body,plan,
            period_bound_plan_v1.prepare_candidate(plan),seal_state)
        return context,receipts,raw

    def test_three_owning_streams_and_final_hmac_still_unavailable(self):
        context,receipts,raw=self.contexts()
        actor,parent,sources,directory,verified,_,_,_,state=context
        query=Mock()
        query.values.return_value.first.return_value=state
        sealed=SimpleNamespace(body_json=raw)
        calls=[]
        def staged(_bridge,window,_manifest,_pages,sink,_current,**kwargs):
            calls.append(window)
            receipt=next(item for item in receipts if item["window"]==window)
            for index in range(receipt["rowCount"]):
                sink.stage("promotion-"+window,index,"{}")
            sink.complete(receipt)
            return receipt
        with patch.object(owning.source_owner,"_v4",
                return_value=context) as verified_call, \
                patch.object(owning.verifier,"_directory",
                    return_value=(actor,parent,sources,directory)), \
                patch.object(owning.source_owner,"_source_manifest",
                    return_value={"manifestDigest":"f"*64}), \
                patch.object(owning.stream,"stage_candidate",
                    side_effect=staged), \
                patch.object(owning.m.AiBusinessV4Seal.objects,"filter",
                    return_value=query), \
                patch.object(owning.m.AiBusinessV4Seal.objects,"get",
                    return_value=sealed):
            value=owning.inspect_candidate(parent.id,
                SimpleNamespace(email=actor["email"],scope=None),
                enabled=True)
        self.assertEqual(calls,list(owning.pure.WINDOWS))
        self.assertEqual(verified_call.call_count,2)
        self.assertEqual(value["status"],
            "unavailable_report_capable_seal_not_issued")
        self.assertTrue(value["threeRealOwningStreamsReplayedTwice"])
        self.assertFalse(value["reportCapableSealIssued"])
        self.assertFalse(value["usableFor13Tables"])
        self.assertFalse(value["usableForAgentOrDownload"])

    def test_business_finance_scope_refuses_before_any_page(self):
        context,_,_=self.contexts()
        changed=list(context)
        changed[5]=deepcopy(context[5])
        finance=next(item for item in changed[5]["sources"]
            if item["domain"]=="finance")
        finance["scope"]={"scope_key":"business",
            "scope_type":"business","scope_name":"集团","group_name":""}
        with patch.object(owning.source_owner,"_v4",
                return_value=tuple(changed)), \
                patch.object(owning.stream,"stage_candidate") as stream, \
                self.assertRaises(AiError):
            owning.inspect_candidate(context[1].id,
                SimpleNamespace(email="admin@example.test",scope=None),
                enabled=True)
        stream.assert_not_called()

    def test_default_and_production_refuse_before_hmac_scan(self):
        context,_,_=self.contexts()
        principal=SimpleNamespace(email="admin@example.test",scope=None)
        with patch.object(owning.source_owner,"_v4") as scan:
            with self.assertRaises(AiError):
                owning.inspect_candidate(context[1].id,principal)
            scan.assert_not_called()
        with override_settings(DJANGO_ENVIRONMENT="production"), \
                self.assertRaises(AiError):
            owning.inspect_candidate(context[1].id,principal,enabled=True)

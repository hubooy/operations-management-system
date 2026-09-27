"""Pure strict answer tests and real sealed reference tests (root PG harness)."""
from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from django import test as djtest
from . import business_screening_diagnosis as service, business_screening_tools as tools
from .test_business_evidence import versioned_netshop_facts
from . import business_screening_claims as claims, test_business_screening_tools as fixtures
from .policy import AiError, canonical


def finding(reference=None,kind="gap"):
    return {"id":"f1","kind":kind,"title":"合成结论","explanation":"缺少因果证据，需观察",
        "references":[] if reference is None else [reference]}


def diagnosis(reference=None,kind="gap"):
    return {"summary":"合成摘要","findings":[finding(reference,kind)]}


def answer(role,reference=None):
    value=diagnosis(reference,"observation" if reference else "gap")
    if role=="report": return {"sections":[{"title":title,"body":"合成范围与缺口"} for title in service.contract.SECTIONS],"diagnosis":value}
    if role=="independent_review": return {"approved":True,"conflicts":[],"limitations":["不是因果证明"]}
    return value


def candidate_evidence(case):
    """Create a fresh real sealed source with deterministic zero-GMV spend."""
    from netshop.models import NetshopRow
    with versioned_netshop_facts():
        NetshopRow.objects.filter(source="jd_promotion").update(net_transaction_amount_cents=0,
            metrics_json={"spendCents":3000,"netTransactionAmountCents":0,"clicks":300,"impressions":3000,"netOrders":30})
    body=deepcopy(case.evidence_body)
    body.update(clientRequestId="screen-content-candidate",sources=deepcopy(case.sources),
        analysisRequest={"schemaVersion":"business-analysis-request-v1","question":"合成有费用零成交",
            "requestedDimensions":["shop","sku","spu"],"requestedWindows":["current"]})
    case.parent=case.collect_body(body)


class ScreeningAnswerTests(TestCase):
    def test_each_role_exact_shape_and_independent_copy(self):
        for role in service.contract.ROLES:
            raw=answer(role);result=service.validate_answer(role,canonical(raw))
            self.assertEqual(result,raw);self.assertIsNot(result,raw)
        with self.assertRaises(AiError):service.validate_answer("alien","{}")

    def test_utf8_and_raw_whitespace_limit_not_character_limit(self):
        raw=canonical(answer("commerce"))
        service.validate_answer("commerce",raw+" "*(2000-len(raw.encode())))
        for changed in (raw+" "*(2001-len(raw.encode())),canonical({**answer("commerce"),"summary":"中"*1000})):
            with self.assertRaises(AiError) as error: service.validate_answer("commerce",changed)
            self.assertEqual(error.exception.status,413)

    def test_duplicate_keys_deep_json_nonfinite_surrogates_and_unknown_fields(self):
        for raw in ('{"summary":"a","summary":"b","findings":[]}', '['*1000+'0'+']'*1000,
                '{"summary":NaN,"findings":[]}','"\\ud800"',canonical({**answer("commerce"),"number":5})):
            with self.subTest(raw=raw[:30]),self.assertRaises(AiError): service.validate_answer("commerce",raw)
        cyclic=[];cyclic.append(cyclic)
        with self.assertRaises(AiError):service._bounded(cyclic,8000)

    def test_specialist_count_and_report_titles_review_boolean(self):
        value=answer("commerce");value["findings"]=[{**finding(),"id":str(i)} for i in range(3)]
        with self.assertRaises(AiError):service.validate_answer("commerce",canonical(value))
        value=answer("report");value["sections"].reverse()
        with self.assertRaises(AiError):service.validate_answer("report",canonical(value))
        for approved in (1,"true",None):
            with self.assertRaises(AiError):service.validate_answer("independent_review",canonical({**answer("independent_review"),"approved":approved}))

    def test_candidate_exact_fields_non_gap_and_mixed_reference_rejection(self):
        ref={"candidateId":"a"*64,"metric":"spendCents","field":"value"}
        service.validate_answer("promotion",canonical(answer("promotion",ref)))
        for change in ({"value":3},{"number":3},{"sourceKey":"ads"},{"field":"ratio"},{"candidateId":"short"}):
            with self.assertRaises(AiError):service.validate_answer("promotion",canonical(answer("promotion",{**ref,**change})))
        with self.assertRaises(AiError):service.validate_answer("commerce",canonical(diagnosis(kind="observation")))

    def test_native_mapped_exact_keys_baseline_and_full_row_id(self):
        native={"sourceKey":"ads","dimension":"sku","rowIndex":0,"rowId":"b"*64,"metric":"spendCents","field":"value"}
        mapped={**native,"pairKey":"c"*64,"metric":"netSalesCents"};mapped.pop("sourceKey")
        for ref in (native,mapped):
            service._reference(ref)
            for change in ({"rowIndex":True},{"rowIndex":0.0},{"rowId":"short"},{"field":"baseline"},{"unknown":1}):
                with self.assertRaises(AiError):service._reference({**ref,**change})
        with self.assertRaises(AiError):service._reference({**mapped,"sourceKey":"sales"})
        with self.assertRaises(AiError):service._reference({**mapped,"field":"ratio"})

    def test_action_exact_nine_fields_strict_days_and_gap_action_rejected(self):
        value=diagnosis({"candidateId":"a"*64,"metric":"spendCents","field":"value"},"action")
        value["findings"][0]["action"]={key:"需观察" for key in service.contract.ACTION_FIELDS}
        value["findings"][0]["action"].update(observationDays=7,priority="high")
        service.validate_answer("commerce",canonical(value))
        for days in (True,0,91,7.0):
            changed=deepcopy(value);changed["findings"][0]["action"]["observationDays"]=days
            with self.assertRaises(AiError):service.validate_answer("commerce",canonical(changed))
        changed=deepcopy(value);changed["findings"][0]["action"].pop("rollback")
        with self.assertRaises(AiError):service.validate_answer("commerce",canonical(changed))
        value["findings"][0]["kind"]="gap"
        with self.assertRaises(AiError):service.validate_answer("commerce",canonical(value))

    def test_resolved_native_zero_negative_missing_and_row_alias(self):
        prepared=SimpleNamespace(report_id="report",reference={"evidenceRunId":"e","reportId":"report","screeningIntent":{"id":"screen"}})
        ref={"sourceKey":"ads","dimension":"sku","rowIndex":0,"rowId":"b"*64,"metric":"spendCents","field":"value"}
        row={"id":ref["rowId"],"rowIndex":0,"metrics":{"spendCents":{"value":0,"missingRows":0}},"entity":{"skuId":"S"}}
        page={"reference":prepared.reference,"mode":"native","selector":{"sourceKey":"ads","dimension":"sku"},
            "table":{"rows":[row],"source":{"sourceRef":"s","evidenceDigest":"d"},"sourceMetadata":{"coverage":{"status":"empty"}},"limitations":["合成"]}}
        with patch.object(tools,"analysis_from",return_value=page):
            for number in (0,-10):
                row["metrics"]["spendCents"]["value"]=number
                result=service._detail(prepared,ref,None,{})
                self.assertEqual(result["value"],number);self.assertFalse(result["verification"]["agentReadVerified"])
            for number in (None,True,float("inf")):
                row["metrics"]["spendCents"]["value"]=number
                with self.assertRaises(AiError):service._detail(prepared,ref,None,{})
            row["id"]="c"*64
            with self.assertRaises(AiError):service._detail(prepared,ref,None,{})


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",DJANGO_ENVIRONMENT="test")
class ScreeningDiagnosisTests(djtest.TransactionTestCase):
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
    seed_ready=fixtures.ScreeningToolsTests.seed_ready

    def test_real_native_mapped_numeric_refs_and_cross_report_rejection(self):
        report=self.seed_ready(mapped=True)
        prepared=tools.prepare_for_report(report,self.admin)
        for mode in ("native","mapped"):
            selector={"sourceKey":"ads"} if mode=="native" else {"pairKey":prepared.snapshot["mappingPlan"]["pairs"][0]["pairKey"]}
            table=tools.analysis_from(prepared,{"runId":self.parent.id,"reportId":report.id,"screeningId":prepared.snapshot["screeningIntent"]["id"],
                "mode":mode,"dimension":"sku",**selector},self.admin)["table"]
            row=table["rows"][0];metric="spendCents" if mode=="native" else "netSalesCents"
            ref={**selector,"dimension":"sku","rowIndex":row["rowIndex"],"rowId":row["id"],"metric":metric,"field":"value"}
            result=service.validate(diagnosis(ref,"observation"),report,self.admin,prepared=prepared)
            self.assertEqual(result["findings"][0]["facts"][0]["value"],row["metrics"][metric]["value"])
            with self.assertRaises(AiError):service.validate(diagnosis({**ref,"rowId":"a"*64},"observation"),report,self.admin,prepared=prepared)
        other=self.seed_ready()
        with self.assertRaises(AiError):service.validate(diagnosis(),other,self.admin,prepared=prepared)

    def test_prepare_many_one_real_rebuild_role_integrity_and_missing_baseline(self):
        candidate_evidence(self)
        report=self.seed_ready(mapped=True)
        prepared=tools.prepare_for_report(report,self.admin)
        original=claims.packages.prepare
        with patch.object(claims.packages,"prepare",wraps=original) as rebuild:
            handles=claims.prepare_many(prepared.packages,list(service.contract.ROLES),self.admin)
        self.assertEqual(rebuild.call_count,1)
        handle=handles["report"]
        import json
        candidates=[item["candidate"] for item in json.loads(handle._index_json)["candidates"].values()]
        self.assertTrue(candidates)
        candidate=next(c for c in candidates if any(v is not None for v in c["current"].values()))
        metric=next(k for k,v in candidate["current"].items() if v is not None)
        ref={"candidateId":candidate["candidateId"],"metric":metric,"field":"value"}
        result=service.validate(diagnosis(ref,"observation"),report,self.admin,prepared=prepared,claims_verified=handle)
        self.assertEqual(result["findings"][0]["facts"][0]["value"],candidate["current"][metric])
        for kwargs in ({"claims_verified":handles["commerce"]},{"claims_verified":{} }):
            with self.assertRaises(AiError):service.validate(diagnosis(ref,"observation"),report,self.admin,prepared=prepared,**kwargs)
        with self.assertRaises(AiError):service.validate(diagnosis({**ref,"field":"baseline"},"observation"),report,self.admin,prepared=prepared,claims_verified=handle)
        with self.assertRaises(AiError):claims.prepare_many(prepared.packages,["report","report"],self.admin)

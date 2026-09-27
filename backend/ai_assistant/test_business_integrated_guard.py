"""Actual PostgreSQL guards; inert fixtures do not enable integrated runtime."""
from copy import deepcopy
from importlib import import_module
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase, override_settings
from netshop.models import NetshopDataRevision, NetshopRow

from business_analysis import mapping_plan
from business_analysis.contracts import digest as object_digest
from business_analysis.test_budget import fixture as budget_fixture
from business_analysis.test_mapping_plan import source as plan_source
from . import business_budget_store as budget_store, business_evidence as evidence, business_reports, models as m, workflows
from . import test_business_identity as identity_fixture
from .test_business_evidence import versioned_netshop_facts
from .policy import AiError, canonical, digest, mutation


PROFILE = "business-agent-integrated-reference-v1"


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessIntegratedGuardTests(TransactionTestCase):
    user = identity_fixture.BusinessIdentityTests.user
    call = identity_fixture.BusinessIdentityTests.call

    def setUp(self):
        NetshopDataRevision.objects.get_or_create(domain="netshop", defaults={"revision": 0, "source_digest": "a"*64})
        identity_fixture.BusinessIdentityTests.setUp(self)
        with versioned_netshop_facts():
            for i in range(2):
                NetshopRow.objects.create(source_row_key=f"integrated-ad-{i}", source_row_hash=digest(["ad",i]),
                    first_import_batch_id="fixture", last_import_batch_id="fixture", source_row_number=i+1,
                    source="jd_promotion", dataset="ad", platform="京东", shop_name=self.query["shop"], business_date="2026-08-01",
                    sku_id=f"S{i}", spu_id="P1", spend_cents=3000, net_transaction_amount_cents=15000,
                    clicks=300, impressions=3000, net_orders=30,
                    metrics_json={"spendCents":3000,"netTransactionAmountCents":15000,"clicks":300,"impressions":3000,"netOrders":30}, raw_json={})
        body = deepcopy(self.evidence_body)
        body["clientRequestId"] = "integrated-evidence"
        body["sources"].append({"key":"ads","domain":"netshop","query":{
            **{k:v for k,v in self.query.items() if k != "channel"},"dataset":"promotion"}})
        self.sources = body["sources"]
        self.parent = self.collect_body(body)
        self.plan = mapping_plan.build(self.sources,[{"salesKey":"sales","masterKey":"master"}])
        self.budget_plan, _ = budget_fixture()
        rows = evidence.analysis_table(self.parent.id,{"sourceKey":"ads","dimension":"sku"},self.admin)["rows"]
        for target, row in zip(self.budget_plan["targets"], rows):
            target.update(rowId=row["id"],rowIndex=row["rowIndex"])
        self.serial = 0

    def collect_body(self, body):
        run = evidence.create(body,self.admin)["item"]
        with patch("ai_assistant.transport.catalog",return_value=self.source_tools), patch(
                "ai_assistant.transport.execute_tool",side_effect=self.source_execute):
            for source in body["sources"]:
                row = evidence.get_run(run["id"],self.admin)
                evidence.collect(row.id,{"sourceKey":source["key"],"expectedVersion":row.version},self.admin,"integrated-guard")
        row = evidence.get_run(run["id"],self.admin)
        evidence.finish(row.id,{"expectedVersion":row.version,"action":"seal"},self.admin)
        return evidence.get_run(row.id,self.admin)

    def bundle(self, *, budget=False):
        self.serial += 1
        report_id = f"integrated-report-{self.serial}"
        reference, _ = business_reports._reference(self.parent,"合成商品关联，不调用模型")
        snapshot = {"schemaVersion":"business-report-v1","executionMode":"parallel-v1","executionProfile":PROFILE,
            "evidenceProtocol":"reference-v2","reportId":report_id,
            **{k:v for k,v in reference.items() if k != "inputMode"},
            "mappingPlan":deepcopy(self.plan["plan"]),"mappingPlanDigest":self.plan["planDigest"]}
        prepared = budget_store.prepare(self.parent,self.budget_plan,self.admin,report_id) if budget else None
        if prepared:
            snapshot["budgetRef"] = prepared.reference
        return snapshot, self.input_for(snapshot), prepared

    def input_for(self, snapshot):
        return {"inputMode":"reference-v2",**{k:snapshot[k] for k in (
            "reportId","question","evidenceRunId","evidenceVersion","evidencePlanDigest","catalogDigest","sealedDigest","sourceCount")},
            "mappingRef":{"schemaVersion":"business-mapping-reference-v1","planDigest":snapshot["mappingPlanDigest"],
                "pairCount":len(snapshot["mappingPlan"]["pairs"])},
            **({"budgetRef":snapshot["budgetRef"]} if "budgetRef" in snapshot else {})}

    def insert(self, bundle, *, snapshot_raw=None, input_raw=None, report_changes=None, flow_changes=None):
        snapshot, data, prepared = bundle
        budget = budget_store.insert(prepared,self.admin) if prepared else None
        flow = {"id":"flow-"+snapshot["reportId"],"owner_email":self.admin.email,"scope_json":"null",
            "client_request_id":"flow-"+snapshot["reportId"],"request_digest":"a"*64,"name":"合成关联存储",
            "graph_json":canonical(business_reports.graph_v2()),"graph_digest":digest(business_reports.graph_v2()),
            "input_json":canonical(data) if input_raw is None else input_raw,"dry_run":1}
        flow.update(flow_changes or {})
        saved = m.AiWorkflowRuns.objects.create(**flow)
        values = {"id":snapshot["reportId"],"owner_email":self.admin.email,"scope_json":"null",
            "client_request_id":snapshot["reportId"],"request_digest":digest(snapshot),"workflow":saved,
            "snapshot_json":canonical(snapshot) if snapshot_raw is None else snapshot_raw,"budget_plan":budget}
        values.update(report_changes or {})
        return m.AiReportRun.objects.create(**values)

    def seed(self, **kwargs):
        bundle = self.bundle(**kwargs)
        with mutation(self.admin):
            return self.insert(bundle),bundle

    def deny(self, bundle, **kwargs):
        before = (m.AiReportRun.objects.count(),m.AiBusinessBudgetPlan.objects.count(),m.AiWorkflowRuns.objects.count())
        with self.assertRaises(DatabaseError),mutation(self.admin):
            self.insert(bundle,**kwargs)
        self.assertEqual(before,(m.AiReportRun.objects.count(),m.AiBusinessBudgetPlan.objects.count(),m.AiWorkflowRuns.objects.count()))

    def test_exact_plan_reference_optional_budget_immutable_and_generic_dispatch_still_closed(self):
        for budget in (False,True):
            report,bundle = self.seed(budget=budget)
            self.assertEqual(report.snapshot_json,canonical(bundle[0]))
            self.assertEqual(bool(report.budget_plan_id),budget)
            if budget:
                binding = json.loads(report.budget_plan.binding_json)
                self.assertEqual(len(binding),13)
                self.assertEqual(binding,bundle[2].binding)
            with self.assertRaises(DatabaseError),transaction.atomic():
                m.AiReportRun.objects.filter(pk=report.id).update(snapshot_json=report.snapshot_json)
            self.assertTrue(business_reports.is_v2_snapshot(bundle[0]))
        with self.assertRaises(AiError):
            workflows.create({"clientRequestId":"integrated-closed","name":"closed","graph":business_reports.graph_v2(),"dryRun":True},
                self.admin,True,execution_profile=PROFILE)

    def test_legacy_reports_and_budget_13_fields_remain_exact(self):
        bundle = self.bundle(budget=True)
        snapshot, data, _ = bundle
        snapshot.update(executionProfile="business-agent-budget-reference-v1")
        snapshot.pop("mappingPlan"); snapshot.pop("mappingPlanDigest"); data.pop("mappingRef")
        with mutation(self.admin): report=self.insert(bundle)
        self.assertEqual(report.snapshot_json,canonical(snapshot))
        self.assertEqual(budget_store.load(report,self.admin).binding,bundle[2].binding)
        for profile in (None,"business-agent-reference-v2","business-agent-budget-reference-v1","future-profile"):
            bad = self.bundle(budget=profile=="business-agent-budget-reference-v1")
            if profile is None: bad[0].pop("executionProfile")
            else: bad[0]["executionProfile"]=profile
            self.deny(bad)

    def test_snapshot_shape_numeric_and_binding_mutations_fail(self):
        mutations = [("mappingPlan",None),("mappingPlanDigest","0"*64),("mappingPlanDigest",True),
            ("reportId","someone-else"),("evidenceRunId","missing"),("evidenceVersion",self.parent.version+1),
            ("evidenceVersion",float(self.parent.version)),("evidenceVersion",True),("sourceCount",3.0),
            ("catalogDigest","0"*64),("evidencePlanDigest","0"*64),("sealedDigest","0"*64),
            ("schemaVersion","future"),("executionMode","serial"),("evidenceProtocol","inline"),
            ("budgetPlan",{}),("mappingRef",{}),("question",None),("extra",1)]
        for key,value in mutations:
            with self.subTest(key=key,value=value):
                bundle=self.bundle(); bundle[0][key]=value; self.deny(bundle)
        for key in ("mappingPlan","mappingPlanDigest","reportId","evidenceVersion","question","sourceCount"):
            with self.subTest(missing=key):
                bundle=self.bundle()
                # Keep the real report id for INSERT while omitting only its JSON field.
                raw=deepcopy(bundle[0]);raw.pop(key)
                self.deny(bundle,snapshot_raw=canonical(raw))

    def test_rehashed_plan_unknown_source_domain_or_pair_is_rejected(self):
        for sales,master in (("sales","ads"),("ads","master"),("master","sales"),("missing","master")):
            bundle=self.bundle(); plan=bundle[0]["mappingPlan"]
            plan["pairs"]=[{"salesKey":sales,"masterKey":master,"pairKey":object_digest([mapping_plan.ALGORITHM_VERSION,sales,master])}]
            bundle[0]["mappingPlanDigest"]=object_digest(plan)
            bundle=(bundle[0],self.input_for(bundle[0]),None)
            with self.subTest(sales=sales,master=master):self.deny(bundle)

    def test_same_plan_changed_directory_cannot_reuse_original_catalog_binding(self):
        body=deepcopy(self.evidence_body);body["clientRequestId"]="different-directory"
        body["sources"][0]["query"]["channel"]="另一个精确渠道"
        changed=self.collect_body(body)
        same=mapping_plan.build(body["sources"],[{"salesKey":"sales","masterKey":"master"}])
        self.assertEqual(same,self.plan)
        self.assertNotEqual(json.loads(changed.plan_json)["catalogDigest"],json.loads(self.parent.plan_json)["catalogDigest"])
        bundle=self.bundle();bundle[0].update(evidenceRunId=changed.id,evidenceVersion=changed.version,evidencePlanDigest=digest(changed.plan_json),
            sealedDigest=json.loads(changed.state_json)["sealedDigest"],sourceCount=2)
        bundle=(bundle[0],self.input_for(bundle[0]),None)
        self.deny(bundle)

    def test_rehashed_pair_cannot_join_other_shop_current_master(self):
        body=deepcopy(self.evidence_body);body["clientRequestId"]="cross-shop-directory"
        other=deepcopy(body["sources"][-1]);other["key"]="other_master";other["query"]["shop"]="另一个精确店铺"
        body["sources"].append(other)
        self.parent=self.collect_body(body)
        bundle=self.bundle();plan=bundle[0]["mappingPlan"]
        plan["pairs"]=[{"salesKey":"sales","masterKey":"other_master",
            "pairKey":object_digest([mapping_plan.ALGORITHM_VERSION,"sales","other_master"])}]
        bundle[0]["mappingPlanDigest"]=object_digest(plan)
        self.deny((bundle[0],self.input_for(bundle[0]),None))

    def test_workflow_binding_owner_scope_unknown_fields_and_references_fail(self):
        for changes in ({"owner_email":"other@example.invalid"},{"scope_json":"{}"}):self.deny(self.bundle(),flow_changes=changes)
        for changes in ({"owner_email":self.admin.email.upper()},{"owner_email":"other@example.invalid"},{"scope_json":"{}"}):
            self.deny(self.bundle(),report_changes=changes)
        for key,value in (("reportId","different"),("evidenceVersion",float(self.parent.version)),("sourceCount",3.0),
                ("catalogDigest","b"*64),("question","changed"),("mappingRef",{}),("mappingPlan",{}),("extra",None)):
            bundle=self.bundle();bundle[1][key]=value
            with self.subTest(key=key):self.deny(bundle)
        for key,value in (("schemaVersion","future"),("planDigest","a"*64),("pairCount",True),("pairCount",1.0),("extra",1)):
            bundle=self.bundle();bundle[1]["mappingRef"][key]=value
            with self.subTest(ref=key):self.deny(bundle)

    def test_budget_reference_and_fk_must_both_exist_with_original_reverse_binding(self):
        bundle=self.bundle();bundle[0]["budgetRef"]={"schemaVersion":"business-budget-reference-v1","id":"missing","planDigest":"a"*64,"bindingDigest":"b"*64}
        self.deny(bundle)
        for value in (None,{},"missing"):
            bundle=self.bundle(budget=True);bundle[0]["budgetRef"]=value;bundle[1]["budgetRef"]=value;self.deny(bundle)
        bundle=self.bundle(budget=True);bundle[0].pop("budgetRef");bundle[1].pop("budgetRef");self.deny(bundle)
        bundle=self.bundle(budget=True)
        with self.assertRaisesRegex(DatabaseError,"orphan"),mutation(self.admin):budget_store.insert(bundle[2],self.admin)

    def test_duplicate_keys_noncanonical_plan_and_input_lexical_numbers_fail(self):
        bundle=self.bundle();raw=canonical(bundle[0]);data=canonical(bundle[1])
        self.deny(bundle,snapshot_raw=raw[:-1]+',"mappingPlanDigest":"'+bundle[0]["mappingPlanDigest"]+'"}')
        for part,replacement in ((canonical(bundle[0]["mappingPlan"]),json.dumps(bundle[0]["mappingPlan"],ensure_ascii=False)),
                ('"salesKey":"sales"','"salesKey":"sales","salesKey":"sales"'),
                ('"pairs":[','"pairs":[],"pairs":[')):
            self.deny(bundle,snapshot_raw=raw.replace(part,replacement,1))
        for part,replacement in (('"pairCount":1','"pairCount":1,"pairCount":1'),
                ('"pairCount":1','"pairCount":1e0'),('"inputMode":"reference-v2"','"inputMode":"reference-v2","inputMode":"reference-v2"')):
            self.deny(bundle,input_raw=data.replace(part,replacement,1))

    def test_sql_plan_canonical_exact_capacity_and_c_collation_match_python(self):
        master="m"*160;keys=[f"s{i:02}" for i in range(47)]
        def build():
            sources=[plan_source(key,channel=f"渠道{i}") for i,key in enumerate(keys)]+[plan_source(master,"netshop")]
            return mapping_plan.normalize(sources,[{"salesKey":key,"masterKey":master} for key in keys])
        remaining=16000-len(canonical(build()).encode())
        for i,key in enumerate(keys):
            add=min(remaining,160-len(key));keys[i]+="x"*add;remaining-=add
        self.assertEqual(remaining,0)
        raw=canonical(build());self.assertEqual(len(raw.encode()),16000)
        with connection.cursor() as cursor:
            cursor.execute("SELECT ai_business_mapping_plan_json(%s)",[raw]);self.assertEqual(cursor.fetchone()[0],raw)

        for bad in (raw+" ","{}",'{"pairs":null}',canonical({**build(),"pairs":[]}),canonical({**build(),"pairs":build()["pairs"]*2})):
            with self.assertRaises(DatabaseError),transaction.atomic(),connection.cursor() as cursor:
                cursor.execute("SELECT ai_business_mapping_plan_json(%s)",[bad])
        keys[:]=["-key","Akey","Zkey","_key","akey","zkey"]
        raw=canonical(build())
        with connection.cursor() as cursor:
            cursor.execute("SELECT ai_business_mapping_plan_json(%s)",[raw]);self.assertEqual(cursor.fetchone()[0],raw)

    def test_sql_plan_shape_and_duplicate_sales_rejected_after_attacker_rehash(self):
        plan=deepcopy(self.plan["plan"])
        bad_plans=[{**plan,"schemaVersion":"future"},{**plan,"algorithmVersion":"future"},
            {**plan,"pairs":None},{**plan,"pairs":[None]}, {**plan,"pairs":[plan["pairs"][0]]*2}]
        for key,value in (("salesKey",True),("masterKey",{}),("salesKey","恶意中文"),("pairKey","a"*64),("extra",1)):
            bad=deepcopy(plan);bad["pairs"][0][key]=value;bad_plans.append(bad)
        for bad in bad_plans:
            with self.subTest(bad=bad),self.assertRaises(DatabaseError),transaction.atomic(),connection.cursor() as cursor:
                cursor.execute("SELECT ai_business_mapping_plan_json(%s)",[canonical(bad)])

    def test_snapshot_and_workflow_exact_byte_caps(self):
        bundle=self.bundle();raw=canonical(bundle[0]);data=canonical(bundle[1])
        with mutation(self.admin):
            self.insert(bundle,snapshot_raw=raw+" "*(32768-len(raw.encode())),input_raw=data+" "*(8000-len(data.encode())))
        bundle=self.bundle();raw=canonical(bundle[0]);data=canonical(bundle[1])
        self.deny(bundle,snapshot_raw=raw+" "*(32769-len(raw.encode())))
        self.deny(bundle,input_raw=data+" "*(8001-len(data.encode())))

    def test_downgrade_restores_original_function_and_refuses_integrated_dependencies(self):
        migration=import_module("ai_assistant.migrations.0022_business_integrated_reports")
        editor=SimpleNamespace(connection=connection)
        with transaction.atomic():
            migration.uninstall(apps,editor)
            with connection.cursor() as cursor:
                cursor.execute("SELECT prosrc FROM pg_proc WHERE proname='ai_business_budget_report_guard'")
                self.assertEqual(cursor.fetchone()[0],migration.OLD_BUDGET_GUARD.split("$$")[1])
            migration.install(apps,editor)
            # Later migrations may have extended these functions. Exercise
            # the historical downgrade inside this savepoint, then restore
            # the schema present on entry instead of leaving a 0022 body.
            transaction.set_rollback(True)
        self.seed()
        with self.assertRaisesRegex(RuntimeError,"integrated"):
            migration.uninstall(apps,editor)
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_trigger WHERE tgname='ai_business_integrated_report_binding' AND tgenabled='O'")
            self.assertEqual(cursor.fetchone()[0],1)

    def test_downgrade_refuses_orphan_workflow_reference_before_any_ddl(self):
        snapshot,data,_=self.bundle()
        m.AiWorkflowRuns.objects.create(id="orphan-integrated-flow",owner_email=self.admin.email,client_request_id="orphan-integrated-flow",
            scope_json="null",request_digest="a"*64,name="inert",graph_json='{"nodes":[]}',graph_digest="a"*64,input_json=canonical(data),dry_run=1)
        migration=import_module("ai_assistant.migrations.0022_business_integrated_reports")
        with self.assertRaisesRegex(RuntimeError,"integrated"):
            migration.uninstall(apps,SimpleNamespace(connection=connection))

"""Real PostgreSQL atomic publication guards over actual sealed report fixtures.

Quota/shape mutations below exercise the database envelope, not a claim that
fabricated candidates or selection digests are accepted by the owning service.
No PostgreSQL process, production source or provider is started by this module.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from importlib import import_module
import json
from threading import Event
import time
from unittest import TestCase

from django.apps import apps
from django.db import DatabaseError, connection, connections, transaction
from django.test import TransactionTestCase, override_settings

from business_analysis import screening_storage as contract
from . import business_diagnostic_screening as screening, models as m
from . import test_business_diagnostic_screening as fixtures
from .database_contract import MODELS, READ_TABLES, WRITER_PRIVILEGES
from .policy import canonical, digest
from .table_manifest import AI_TABLES, AI_TABLES_PRE_TOOL_RECEIPTS, AI_TABLES_PRE_V3_REPORT_INTENTS, AI_TABLES_PRE_V4_LEDGER, AI_TABLES_PRE_V4_VALIDATION, AI_TABLES_PRE_V4_SEALS, AI_TABLES_PRE_V4_TICKETS, AI_TABLES_PRE_V4_CONSUMPTIONS, AI_TABLES_PRE_MARKET_V2_MATERIALS


def row_values(bundle, tag):
    binding, manifest = json.loads(bundle["bindingJson"]), json.loads(bundle["manifestJson"])
    return {"id":"screen-"+str(tag), "report_id":binding["reportId"], "evidence_id":binding["evidenceRunId"],
        "owner_email":binding["ownerEmail"], "scope_json":canonical(binding["scope"]),
        "binding_json":bundle["bindingJson"], "binding_digest":digest(bundle["bindingJson"]),
        "manifest_json":bundle["manifestJson"], "manifest_digest":digest(bundle["manifestJson"]),
        "selection_plan_digest":manifest["selectionPlanDigest"], "pure_result_digest":manifest["pureResultDigest"],
        "service_result_digest":manifest["serviceResultDigest"], "content_root_digest":manifest["contentRootDigest"],
        "algorithm_version":manifest["algorithmVersion"], "selection_policy":manifest["selectionPolicy"],
        "storage_schema":contract.SCHEMA, "capacity_profile":manifest["capacityProfile"],
        "page_count":manifest["pageCount"], "stored_bytes":bundle["storedBytes"]}


def page_values(page, run_id):
    return {"id":run_id+"-"+str(page["sequence"]), "run_id":run_id, "sequence":page["sequence"],
        "kind":page["kind"], "partition_key":page["partitionKey"], "offset":page["offset"],
        "returned":page["returned"], "total":page["total"], "next_offset":page["nextOffset"],
        "payload_json":page["payloadJson"], "payload_digest":page["payloadDigest"]}


def reframe(bundle):
    """Rehash storage envelopes so a negative test reaches semantic guards."""
    manifest = json.loads(bundle["manifestJson"])
    root = contract.INITIAL_CHAIN
    for page in bundle["pages"]:
        page["payloadDigest"] = digest(page["payloadJson"])
        root = contract.chain(root,page["sequence"],page["payloadDigest"])
    for group in manifest["groups"]:
        chain = contract.INITIAL_CHAIN
        for i, page in enumerate(bundle["pages"][group["firstSequence"]-1:group["lastSequence"]],1):
            chain = contract.chain(chain,i,page["payloadDigest"])
        group["pagesDigest"] = chain
    manifest["contentRootDigest"] = root
    bundle["manifestJson"] = canonical(manifest)
    bundle["storedBytes"] = len(bundle["bindingJson"].encode())+len(bundle["manifestJson"].encode())+sum(
        len(page["payloadJson"].encode()) for page in bundle["pages"])
    return bundle


def variant(bundle, tag):
    result = deepcopy(bundle)
    manifest = json.loads(result["manifestJson"])
    manifest["selectionPlanDigest"] = digest(["storage-only-quota",tag])
    result["manifestJson"] = canonical(manifest)
    for page in result["pages"]:
        value = json.loads(page["payloadJson"])
        value["planDigest"] = manifest["selectionPlanDigest"]
        value["authority"]["selectionPlanDigest"] = manifest["selectionPlanDigest"]
        value["pageDigest"] = digest({k:v for k,v in value.items() if k!="pageDigest"})
        page["payloadJson"] = canonical(value)
    return reframe(result)


class InventoryTests(TestCase):
    def test_exact_models_tables_and_append_only_grants(self):
        self.assertEqual(len(AI_TABLES),78)
        self.assertEqual(len(AI_TABLES_PRE_TOOL_RECEIPTS), 65)
        self.assertEqual(len(AI_TABLES_PRE_V3_REPORT_INTENTS), 66)
        self.assertEqual(set(AI_TABLES_PRE_V3_REPORT_INTENTS) - set(AI_TABLES_PRE_TOOL_RECEIPTS),
                         {"ai_business_source_tool_receipts"})
        self.assertEqual(set(AI_TABLES_PRE_V4_SEALS) - set(AI_TABLES_PRE_V3_REPORT_INTENTS),
                         {"ai_business_v3_report_intents", "ai_business_v4_runs", "ai_business_v4_sources",
                          "ai_business_v4_chunks", "ai_business_v4_tool_receipts",
                          "ai_business_v4_validation_attempts", "ai_business_v4_validation_segments"})
        self.assertEqual(len(AI_TABLES_PRE_V4_LEDGER), 67)
        self.assertEqual(len(AI_TABLES_PRE_V4_VALIDATION), 71)
        self.assertEqual(set(AI_TABLES_PRE_V4_VALIDATION) - set(AI_TABLES_PRE_V4_LEDGER),
                         {"ai_business_v4_runs", "ai_business_v4_sources", "ai_business_v4_chunks",
                          "ai_business_v4_tool_receipts"})
        self.assertEqual(len(AI_TABLES_PRE_V4_SEALS), 73)
        self.assertEqual(set(AI_TABLES_PRE_V4_SEALS) - set(AI_TABLES_PRE_V4_VALIDATION),
                         {"ai_business_v4_validation_attempts", "ai_business_v4_validation_segments"})
        self.assertEqual(len(AI_TABLES_PRE_V4_TICKETS), 74)
        self.assertEqual(set(AI_TABLES_PRE_V4_TICKETS) - set(AI_TABLES_PRE_V4_SEALS),
                          {"ai_business_v4_seals"})
        self.assertEqual(len(AI_TABLES_PRE_V4_CONSUMPTIONS), 76)
        self.assertEqual(set(AI_TABLES_PRE_V4_CONSUMPTIONS) - set(AI_TABLES_PRE_V4_TICKETS),
                         {"ai_business_v4_seal_tickets", "ai_business_v4_seal_claims"})
        self.assertEqual(len(AI_TABLES_PRE_MARKET_V2_MATERIALS), 77)
        self.assertEqual(set(AI_TABLES_PRE_MARKET_V2_MATERIALS) - set(AI_TABLES_PRE_V4_CONSUMPTIONS),
                         {"ai_business_v4_seal_consumptions"})
        self.assertEqual(set(AI_TABLES) - set(AI_TABLES_PRE_MARKET_V2_MATERIALS),
                         {"ai_business_market_v2_materials"})
        self.assertEqual(set(AI_TABLES),set(MODELS))
        for table in ("ai_business_v4_seal_tickets", "ai_business_v4_seal_claims",
                      "ai_business_v4_seal_consumptions",
                      "ai_business_market_v2_materials"):
            self.assertNotIn(table, READ_TABLES)
            self.assertNotIn(table, WRITER_PRIVILEGES)
        for table, model in (("ai_business_screening_runs",m.AiBusinessScreeningRun),
                ("ai_business_screening_pages",m.AiBusinessScreeningPage)):
            self.assertIs(MODELS[table],model)
            self.assertIn(table,READ_TABLES)
            self.assertEqual(WRITER_PRIVILEGES[table],("SELECT","INSERT"))

    def test_only_quota_revision_lock_requires_update_privilege(self):
        migration=import_module("ai_assistant.migrations.0023_business_screening_storage")
        self.assertIn("ai_data_revisions WHERE domain='ai-assistant' FOR UPDATE",migration.INITIAL)
        for definition in (migration.PAGE,migration.COMPLETE):
            self.assertNotIn("FOR UPDATE",definition)
            self.assertNotIn("FOR SHARE",definition)
            self.assertNotIn("FOR KEY SHARE",definition)
            self.assertNotIn("FOR NO KEY UPDATE",definition)


@override_settings(DJANGO_PROCESS_ROLE="development",DJANGO_ENVIRONMENT="test")
class ScreeningStorageTests(TransactionTestCase):
    user = fixtures.DiagnosticScreeningTests.user
    call = fixtures.DiagnosticScreeningTests.call
    collect_body = fixtures.DiagnosticScreeningTests.collect_body
    bundle = fixtures.DiagnosticScreeningTests.bundle
    input_for = fixtures.DiagnosticScreeningTests.input_for
    insert = fixtures.DiagnosticScreeningTests.insert
    seed = fixtures.DiagnosticScreeningTests.seed

    def setUp(self):
        fixtures.DiagnosticScreeningTests.setUp(self)
        verified = screening.prepare_for_report(self.report.id,self.admin)
        self.base_bundle = contract.materialize(json.loads(verified._result_json))

    def insert_bundle(self, bundle=None, tag="valid", *, run_changes=None, page_changes=None, skip_last=False):
        bundle = bundle or self.base_bundle
        values = row_values(bundle,tag)
        values.update(run_changes or {})
        run = m.AiBusinessScreeningRun.objects.create(**values)
        for page in bundle["pages"][:-1] if skip_last else bundle["pages"]:
            fields = page_values(page,run.id)
            fields.update(page_changes or {})
            m.AiBusinessScreeningPage.objects.create(**fields)
        return run

    def denied(self, bundle=None, **kwargs):
        counts=(m.AiBusinessScreeningRun.objects.count(),m.AiBusinessScreeningPage.objects.count())
        with self.assertRaises(DatabaseError),transaction.atomic():
            self.insert_bundle(bundle,**kwargs)
        self.assertEqual(counts,(m.AiBusinessScreeningRun.objects.count(),m.AiBusinessScreeningPage.objects.count()))

    def test_complete_actual_pages_commit_and_cannot_update_delete_or_append(self):
        with transaction.atomic(): run=self.insert_bundle()
        self.assertEqual(run.stored_bytes,self.base_bundle["storedBytes"])
        self.assertEqual(m.AiBusinessScreeningPage.objects.filter(run=run).count(),run.page_count)
        page=m.AiBusinessScreeningPage.objects.filter(run=run).first()
        for fn in (lambda:m.AiBusinessScreeningRun.objects.filter(pk=run.id).update(stored_bytes=run.stored_bytes),
                lambda:m.AiBusinessScreeningPage.objects.filter(pk=page.id).update(payload_json=page.payload_json),
                lambda:m.AiBusinessScreeningPage.objects.filter(pk=page.id).delete()):
            with self.assertRaises(DatabaseError),transaction.atomic(): fn()
        with self.assertRaises(DatabaseError),transaction.atomic(),connection.cursor() as cursor:
            cursor.execute("DELETE FROM ai_business_screening_runs WHERE id=%s",[run.id])
        extra=page_values(self.base_bundle["pages"][0],run.id)
        extra.update(id="screen-extra",sequence=run.page_count+1)
        with self.assertRaises(DatabaseError),transaction.atomic(): m.AiBusinessScreeningPage.objects.create(**extra)

    def test_parent_without_all_pages_and_late_exception_publish_zero(self):
        self.denied(skip_last=True)
        with self.assertRaises(RuntimeError),transaction.atomic():
            self.insert_bundle()
            raise RuntimeError("synthetic audit failure")
        self.assertFalse(m.AiBusinessScreeningRun.objects.exists())
        self.assertFalse(m.AiBusinessScreeningPage.objects.exists())

    def test_other_transaction_cannot_supply_pages_to_uncommitted_parent(self):
        values=page_values(self.base_bundle["pages"][0],"screen-uncommitted")
        values["id"]="screen-concurrent-child"
        def competing_child():
            connections.close_all()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor: cursor.execute("SET LOCAL statement_timeout='5s'")
                    m.AiBusinessScreeningPage.objects.create(**values)
                return "unexpected-success"
            except DatabaseError as error:return str(error)
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                run=self.insert_bundle(tag="uncommitted")
                # The independent INSERT cannot see this uncommitted parent;
                # it must fail, not wait for or help finish its publication.
                result=pool.submit(competing_child).result(timeout=7)
                self.assertIn("ai_screen_parent_missing",result)
        self.assertEqual(m.AiBusinessScreeningPage.objects.filter(run=run).count(),run.page_count)
        self.assertFalse(m.AiBusinessScreeningPage.objects.filter(pk=values["id"]).exists())

    def test_binding_rehashed_tampering_cannot_change_actual_authority(self):
        mutations={"ownerEmail":"other@example.invalid","scope":{},"role":"viewer","reportId":"missing",
            "workflowId":"missing","snapshotDigest":"a"*64,"workflowInputDigest":"b"*64,
            "evidenceRunId":"missing","evidenceVersion":True,"catalogDigest":"a"*64,"sealedDigest":"a"*64,
            "sourceCount":None,"analysisRequestDigest":None,"mappingPlanDigest":None,"budgetRef":{},
            "algorithmVersion":"unknown","extra":"invalid"}
        for key,value in mutations.items():
            with self.subTest(key=key):
                bad=deepcopy(self.base_bundle)
                binding=json.loads(bad["bindingJson"]);binding[key]=value
                bad["bindingJson"]=canonical(binding)
                manifest=json.loads(bad["manifestJson"]);manifest["bindingDigest"]=digest(binding)
                bad["manifestJson"]=canonical(manifest)
                reframe(bad)
                self.denied(bad)

    def test_wrong_sequences_page_hash_and_complete_actual_bytes_rejected(self):
        self.denied(page_changes={"sequence":2})
        self.denied(page_changes={"payload_digest":"a"*64})
        self.denied(run_changes={"stored_bytes":self.base_bundle["storedBytes"]+1})
        self.denied(run_changes={"page_count":len(self.base_bundle["pages"])+1})
        self.denied(run_changes={"algorithm_version":"unknown"})
        self.denied(run_changes={"capacity_profile":"unknown"})
        self.denied(run_changes={"storage_schema":"unknown"})

    def test_json_null_duplicate_and_decimal_integer_fail_closed(self):
        for literal in ("null","true","1.0","1e0"):
            with self.subTest(literal=literal):
                bad=deepcopy(self.base_bundle)
                binding=json.loads(bad["bindingJson"])
                original='"evidenceVersion":'+str(binding["evidenceVersion"])
                bad["bindingJson"]=bad["bindingJson"].replace(original,'"evidenceVersion":'+literal)
                manifest=json.loads(bad["manifestJson"]);manifest["bindingDigest"]=digest(bad["bindingJson"])
                bad["manifestJson"]=canonical(manifest)
                self.denied(reframe(bad))
        bad=deepcopy(self.base_bundle)
        bad["bindingJson"]='{"role":"admin",'+bad["bindingJson"][1:]
        self.denied(reframe(bad))
        for literal in ("null","true","1.0"):
            bad=deepcopy(self.base_bundle)
            raw=json.loads(bad["pages"][0]["payloadJson"])
            token='"limit":20'
            bad["pages"][0]["payloadJson"]=canonical(raw).replace(token,'"limit":'+literal)
            self.denied(reframe(bad))

    def test_rehashed_manifest_group_gap_order_counts_and_root_rejected(self):
        for change in ("gap","order","total","chain","root","omit_partition"):
            with self.subTest(change=change):
                bad=deepcopy(self.base_bundle);manifest=json.loads(bad["manifestJson"])
                if change=="gap": manifest["groups"][0]["lastSequence"]+=1
                elif change=="order": manifest["groups"]=list(reversed(manifest["groups"]))
                elif change=="total": manifest["groups"][0]["total"]+=1
                elif change=="chain": manifest["groups"][0]["pagesDigest"]="a"*64
                elif change=="root": manifest["contentRootDigest"]="a"*64
                else: manifest["groups"].pop()
                bad["manifestJson"]=canonical(manifest)
                bad["storedBytes"]=len(bad["bindingJson"].encode())+len(bad["manifestJson"].encode())+sum(len(p["payloadJson"].encode()) for p in bad["pages"])
                self.denied(bad)

    def test_rehashed_page_range_partition_and_coverage_mismatch_rejected(self):
        for change in ("offset","next","partition","coverage","authority","unexecuted","tables"):
            with self.subTest(change=change):
                bad=deepcopy(self.base_bundle)
                page=bad["pages"][0] if change!="partition" else next(p for p in bad["pages"] if p["kind"]=="candidates")
                value=json.loads(page["payloadJson"])
                if change=="offset": value["pagination"]["offset"]=1
                elif change=="next": value["pagination"]["nextOffset"]=True
                elif change=="partition": value["partition"]["partitionKey"]="a"*64
                elif change=="authority": value["authority"]["tableCount"]+=1
                elif change=="unexecuted": value["authority"]["executedTablesComplete"]=False
                elif change=="tables":
                    for p in bad["pages"]:
                        if p["kind"]!="coverage": continue
                        item=json.loads(p["payloadJson"])
                        tables=[r for r in item["items"] if r["kind"]=="table"]
                        if tables:
                            tables[0]["value"]["tableKey"]="b"*64
                            p["payloadJson"]=canonical(item);break
                    else:self.fail("fixture lacks actual coverage tables")
                else:
                    for p in bad["pages"]:
                        if p["kind"]!="coverage": continue
                        item=json.loads(p["payloadJson"])
                        candidates=[r for r in item["items"] if r["kind"]=="partition"]
                        if candidates:
                            candidates[0]["value"]["matchedRows"]+=1
                            p["payloadJson"]=canonical(item);break
                    else: self.fail("fixture lacks actual coverage partitions")
                if change not in {"coverage","tables"}: page["payloadJson"]=canonical(value)
                self.denied(reframe(bad))

    def test_utf8_binding_manifest_page_and_parent_bounds(self):
        self.denied(run_changes={"binding_json":" "*8193})
        self.denied(run_changes={"manifest_json":" "*65537})
        self.denied(page_changes={"payload_json":"中"*12667})
        self.denied(run_changes={"stored_bytes":16*1024*1024+1})
        self.denied(run_changes={"page_count":4097})

    def test_unique_binding_and_owner_row_quota(self):
        with transaction.atomic(): self.insert_bundle()
        self.denied(tag="same-fixed-binding")
        with transaction.atomic():
            for i in range(19): self.insert_bundle(variant(self.base_bundle,i),tag=i)
        self.denied(variant(self.base_bundle,"over"),tag="over")
        self.assertEqual(m.AiBusinessScreeningRun.objects.count(),20)

    def test_concurrent_quota_observes_winner_commit_after_real_lock_wait(self):
        with transaction.atomic():
            for i in range(19): self.insert_bundle(variant(self.base_bundle,i),tag=i)
        loser=variant(self.base_bundle,"loser")
        started,pids=Event(),[]
        def competing():
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()");pids.append(cursor.fetchone()[0])
                started.set()
                with transaction.atomic():
                    with connection.cursor() as cursor: cursor.execute("SET LOCAL statement_timeout='10s'")
                    self.insert_bundle(loser,tag="loser")
                return "unexpected-success"
            except DatabaseError as error: return str(error)
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                self.insert_bundle(variant(self.base_bundle,"winner"),tag="winner")
                future=pool.submit(competing)
                self.assertTrue(started.wait(5))
                deadline,blocked=time.monotonic()+5,False
                while time.monotonic()<deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s",[pids[0]])
                        row=cursor.fetchone()
                    if row==("Lock",): blocked=True;break
                    time.sleep(.01)
                self.assertTrue(blocked)
            self.assertIn("quota",future.result(timeout=12))
        self.assertEqual(m.AiBusinessScreeningRun.objects.count(),20)

    def test_repeatable_read_and_reverse_are_closed(self):
        with self.assertRaisesRegex(DatabaseError,"isolation"),transaction.atomic():
            with connection.cursor() as cursor: cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            self.insert_bundle()
        with transaction.atomic(): self.insert_bundle()
        migration=import_module("ai_assistant.migrations.0023_business_screening_storage")
        with self.assertRaisesRegex(RuntimeError,"screening"),connection.schema_editor() as editor:
            migration.uninstall(apps,editor)
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_trigger WHERE tgname='ai_screen_complete'")
            self.assertEqual(cursor.fetchone()[0],2)


def sized_bundle(original, target):
    """Synthetic valid storage pages of an exact charged size, not real facts."""
    bundle=deepcopy(original)
    manifest=json.loads(bundle["manifestJson"])
    coverage=[json.loads(p["payloadJson"]) for p in bundle["pages"] if p["kind"]=="coverage"]
    rows=[row for page in coverage for row in page["items"]]
    fixed_rows=len(rows)
    base={k:v for k,v in coverage[0].items() if k not in {"items","pagination","pageDigest"}}
    candidates=[p for p in bundle["pages"] if p["kind"]=="candidates"]
    # The fixed-size padding row is intentionally not advertised as an owning
    # result. It tests actual UTF-8 charge, including every authority envelope.
    sample={"kind":"family","value":{"quotaPadding":"x"*34000}}
    # Estimate using this fixture's actual authority size, then reserve one
    # extra page. This avoids dozens of repeated 16 MiB serializations.
    page_bytes=len(canonical(contract._page(base,[sample],0)).encode())+8
    n=max(1,(target-original["storedBytes"]+page_bytes-1)//page_bytes+1)
    rows.extend({"kind":"family","value":{"quotaPadding":"x"*34000}} for _ in range(n))

    def render():
        pages=[];offset=0
        while True:
            payload=contract._page(base,rows,offset);meta=payload["pagination"]
            pages.append({"sequence":len(pages)+1,"kind":"coverage","partitionKey":"",
                **{k:meta[k] for k in ("offset","returned","total","nextOffset")},
                "payloadJson":canonical(payload),"payloadDigest":digest(payload)})
            if meta["nextOffset"] is None:break
            offset=meta["nextOffset"]
        old_count=manifest["groups"][0]["pageCount"]
        added=len(pages)-old_count
        groups=deepcopy(manifest["groups"])
        groups[0].update(total=len(rows),pageCount=len(pages),lastSequence=len(pages))
        for group in groups[1:]:
            group["firstSequence"]+=added;group["lastSequence"]+=added
        for page in candidates:
            pages.append({**page,"sequence":page["sequence"]+added})
        header={**manifest,"groups":groups,"pageCount":len(pages)}
        return reframe({"bindingJson":bundle["bindingJson"],"manifestJson":canonical(header),"pages":pages,"storedBytes":0})

    result=render()
    # Reserve enough complete padding rows. Spread the final byte adjustment
    # across the large rows so none shrinks enough to change page grouping.
    while result["storedBytes"]<target:
        rows.append({"kind":"family","value":{"quotaPadding":"x"*34000}})
        result=render()
    excess=result["storedBytes"]-target
    per_row,remainder=divmod(excess,len(rows)-fixed_rows)
    for i,row in enumerate(rows[fixed_rows:]):
        reduction=per_row+(1 if i<remainder else 0)
        if reduction>1000:raise AssertionError("quota fixture target too small")
        row["value"]["quotaPadding"]="x"*(34000-reduction)
    result=render()
    if result["storedBytes"]!=target:raise AssertionError((result["storedBytes"],target))
    return result


@override_settings(DJANGO_PROCESS_ROLE="development",DJANGO_ENVIRONMENT="test")
class ScreeningStorageQuotaTests(TransactionTestCase):
    """Separate label for bounded 64/256 MiB real-storage quota exercises."""
    user=ScreeningStorageTests.user
    call=ScreeningStorageTests.call
    collect_body=ScreeningStorageTests.collect_body
    bundle=ScreeningStorageTests.bundle
    input_for=ScreeningStorageTests.input_for
    insert=ScreeningStorageTests.insert
    seed=ScreeningStorageTests.seed
    setUp=ScreeningStorageTests.setUp
    insert_bundle=ScreeningStorageTests.insert_bundle
    denied=ScreeningStorageTests.denied

    def owner_bundle(self,index):
        self.admin=self.user(f"screen-quota-{index}@example.invalid","admin",None)
        body=deepcopy(self.evidence_body)
        body.update(clientRequestId=f"screen-owner-{index}",sources=deepcopy(self.sources),
            analysisRequest={"schemaVersion":"business-analysis-request-v1","question":"合成存储额度",
                "requestedDimensions":["shop","sku","spu"],"requestedWindows":["current"]})
        self.parent=self.collect_body(body)
        report,_=self.seed()
        verified=screening.prepare_for_report(report.id,self.admin)
        return contract.materialize(json.loads(verified._result_json))

    def test_owner_exact_byte_quota_counts_all_headers_and_pages(self):
        wide=sized_bundle(self.base_bundle,16*1024*1024)
        for i in range(4):
            with transaction.atomic():self.insert_bundle(variant(wide,i),tag=f"wide-{i}")
        with connection.cursor() as cursor:
            cursor.execute("SELECT sum(stored_bytes) FROM ai_business_screening_runs")
            self.assertEqual(cursor.fetchone()[0],64*1024*1024)
        self.denied(variant(self.base_bundle,"over"),tag="over")

    def test_global_exact_byte_quota_rejects_an_unused_owner(self):
        for owner in range(4):
            source=self.base_bundle if owner==0 else self.owner_bundle(owner)
            wide=sized_bundle(source,16*1024*1024)
            for i in range(4):
                with transaction.atomic():self.insert_bundle(variant(wide,[owner,i]),tag=f"global-{owner}-{i}")
        fresh=self.owner_bundle(4)
        self.assertFalse(m.AiBusinessScreeningRun.objects.filter(owner_email=self.admin.email).exists())
        with connection.cursor() as cursor:
            cursor.execute("SELECT sum(stored_bytes) FROM ai_business_screening_runs")
            self.assertEqual(cursor.fetchone()[0],256*1024*1024)
        self.denied(fresh,tag="global-over")

    def test_global_count_quota_is_independent_of_owner_count(self):
        for owner in range(10):
            source=self.base_bundle if owner==0 else self.owner_bundle(owner)
            with transaction.atomic():
                for i in range(20):self.insert_bundle(variant(source,[owner,i]),tag=f"count-{owner}-{i}")
        fresh=self.owner_bundle(10)
        self.assertFalse(m.AiBusinessScreeningRun.objects.filter(owner_email=self.admin.email).exists())
        self.denied(fresh,tag="count-over")
        self.assertEqual(m.AiBusinessScreeningRun.objects.count(),200)

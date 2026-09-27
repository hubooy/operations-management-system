from copy import deepcopy
import hashlib
import io
import json
import re
import tempfile
from unittest import TestCase
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

from . import volume_files, volume_plan
from .budget import calculate
from .budget_offline import payload
from .contracts import AnalysisContractError, canonical, digest
from .report_files import Column, Table, NS
from .test_budget import fixture as budget_fixture
from .test_report_files import ReportData
from .volume_files import VolumeStreams, render, request_for


class OnePass:
    """A source that fails if reopened or converted using sequence machinery."""
    def __init__(self, count, make=None):
        self.count, self.make = count, make or (lambda i: [f"行{i}", i])
        self.iterations, self.position, self.eof_checks = 0, 0, 0

    def __iter__(self):
        self.iterations += 1
        if self.iterations != 1:
            raise AssertionError("Source reopened")
        return self

    def __len__(self):
        raise AssertionError("Source materialized")

    def __next__(self):
        if self.position == self.count:
            self.eof_checks += 1
            raise StopIteration
        value = self.make(self.position)
        self.position += 1
        return value


def table(key, count=1, actual=None, make=None):
    return Table(key, "完整表"+key, "仅合成来源", (Column("name", "对象"), Column("amount", "金额（分）", "integer", True)),
                 OnePass(count if actual is None else actual, make), count)


def prepare(tables, **policy):
    request = request_for(tables, report_id="report-volume-test", evidence_digest="a"*64, renderer_version=4)
    plan = volume_plan.build(request, **policy)
    outputs = [VolumeStreams(io.BytesIO(), io.BytesIO()) for _ in plan["volumes"]]
    return plan, outputs


def run(tables, outputs, plan, **kwargs):
    return render(tables, outputs, plan=plan, report_id="report-volume-test", evidence_digest="a"*64, renderer_version=4,
                  title="合成多卷报告", metadata={"synthetic": True}, **kwargs)


class VolumeFilesTests(TestCase):
    def test_only_renderer_four_selects_narrow_layout(self):
        for renderer_version in (1, 2, 3, 4):
            tables = [table("layout")]
            request = request_for(tables, report_id="layout", evidence_digest="a"*64, renderer_version=renderer_version)
            plan = volume_plan.build(request)
            outputs = [VolumeStreams(io.BytesIO(), io.BytesIO())]
            render(tables, outputs, report_id="layout", evidence_digest="a"*64, renderer_version=renderer_version,
                plan=plan, title="合成宽列", metadata={})
            self.assertEqual(b"business-html-layout-v2" in outputs[0].html.getvalue(), renderer_version == 4)

    def test_131_156_tables_all_preserved_and_actual_file_hashes(self):
        for count in (131, 156):
            with self.subTest(count=count):
                tables = [table(str(i), i % 3) for i in range(count)]
                plan, outputs = prepare(tables)
                proof = run(tables, outputs, plan)
                self.assertEqual(proof["status"], "complete")
                self.assertEqual(proof["volumeCount"], 2)
                self.assertEqual([len(v["tables"]) for v in proof["volumes"]], [120, count-120])
                self.assertEqual(proof["totalRows"], sum(t.row_count for t in tables))
                self.assertEqual([t["key"] for t in proof["tables"]], [t.key for t in tables])
                self.assertEqual(proof["manifestDigest"], digest({k:v for k,v in proof.items() if k != "manifestDigest"}))
                for volume, streams in zip(proof["volumes"], outputs):
                    document = ReportData(streams.html.getvalue().decode()).value
                    self.assertEqual(document["metadata"]["volumeDelivery"]["planDigest"], plan["planDigest"])
                    self.assertEqual(document["metadata"]["volumeDelivery"]["publication"], "requires_complete_multivolume_manifest")
                    self.assertEqual(document["metadata"]["volumeDelivery"]["volumeIndex"], volume["volumeIndex"])
                    with zipfile.ZipFile(streams.xlsx) as archive:
                        self.assertIsNone(archive.testzip())
                        manifest = json.loads(archive.read("teruisi-manifest.json"))
                        self.assertEqual([t["proof"] for t in document["tables"]], manifest["tables"])
                    for format, stream in (("html", streams.html), ("xlsx", streams.xlsx)):
                        raw = stream.getvalue()
                        self.assertEqual(volume["files"][format]["bytes"], len(raw))
                        self.assertEqual(volume["files"][format]["sha256"], hashlib.sha256(raw).hexdigest())
                self.assertTrue(all(t.rows.iterations == 1 and t.rows.eof_checks == 1 for t in tables))

    def test_one_source_crosses_volumes_with_exact_order_digests_and_empty_table(self):
        tables = [table("long", 9, make=lambda i: ["=source-"+str(i), 10**16+i]), table("empty", 0), table("tail", 1)]
        plan, outputs = prepare(tables, max_tables=2, max_rows=2)
        proof = run(tables, outputs, plan, max_tables=2, max_rows=2)
        fragments = [t for v in proof["volumes"] for t in v["tables"]]
        self.assertEqual([(t["key"],t["rowOffset"],t["rowLimit"]) for t in fragments],
                         [("long",0,2),("long",2,2),("long",4,2),("long",6,2),("long",8,1),("empty",0,0),("tail",0,1)])
        actual = []
        for output in outputs:
            for part in ReportData(output.html.getvalue().decode()).value["tables"]:
                actual.extend(part["rows"])
                expected = hashlib.sha256(''.join(canonical(row)+'\n' for row in part["rows"]).encode()).hexdigest()
                self.assertEqual(part["proof"]["rowDigest"], expected)
        self.assertEqual(actual, [["=source-"+str(i), str(10**16+i)] for i in range(9)]+[["行0",0]])
        self.assertEqual(proof["tables"][0]["rowDigest"], hashlib.sha256(''.join(canonical(row)+'\n' for row in actual[:9]).encode()).hexdigest())
        self.assertEqual(proof["tables"][1]["rowDigest"], hashlib.sha256(b"").hexdigest())
        self.assertEqual([t.rows.iterations for t in tables], [1,1,1])
        self.assertEqual([t.rows.position for t in tables], [9,0,1])

    def test_budget_reserved_only_first_volume_and_fragments_still_complete(self):
        plan_input, bases = budget_fixture()
        budget = payload(calculate(plan_input, bases), "report-volume-test")
        budget["excelEnabled"] = True
        tables = [table("long", 5)]
        plan, outputs = prepare(tables, max_tables=4, max_rows=2, native_budget_sheets=3)
        proof = run(tables, outputs, plan, max_tables=4, max_rows=2, offline_budget=budget, excel_budget=budget)
        self.assertEqual([v["nativeBudgetSheets"] for v in proof["volumes"]], [3,0])
        self.assertEqual([v["offlineBudgetEnabled"] for v in proof["volumes"]], [True,False])
        self.assertIn("budgetCalculator", proof["volumes"][0])
        self.assertNotIn("budgetCalculator", proof["volumes"][1])
        for i, output in enumerate(outputs):
            self.assertEqual('id="budget-data"' in output.html.getvalue().decode(), i==0)
            with zipfile.ZipFile(output.xlsx) as archive:
                workbook = ET.fromstring(archive.read("xl/workbook.xml"))
                self.assertEqual(len(workbook.find('{'+NS+'}sheets')), 4 if i==0 else 2)

    def test_budget_only_rejected_before_any_source_or_stream_write(self):
        p,b = budget_fixture(); budget = payload(calculate(p,b), "report-volume-test")
        for tables in ([table("first", 1)], []):
            plan, outputs = prepare(tables, max_tables=3, native_budget_sheets=3)
            with self.assertRaisesRegex(AnalysisContractError,"budget_only"):
                run(tables, outputs, plan, max_tables=3, excel_budget=budget)
            self.assertTrue(all(not o.xlsx.getvalue() and not o.html.getvalue() for o in outputs))
            self.assertTrue(all(t.rows.iterations==0 for t in tables))

    def test_late_short_and_extra_rows_refuse_all_completed_delivery_receipts(self):
        for actual in (4,6):
            tables = [table("first",1),table("late",5,actual)]
            plan, outputs = prepare(tables,max_tables=1,max_rows=2)
            absent = object(); result = absent
            with self.assertRaisesRegex(AnalysisContractError,"迟到"):
                result = run(tables,outputs,plan,max_tables=1,max_rows=2)
            self.assertIs(result,absent)
            with zipfile.ZipFile(outputs[0].xlsx) as archive:
                self.assertIsNone(archive.testzip())
                metadata = json.loads(archive.read('teruisi-manifest.json'))['metadata']
                self.assertEqual(metadata['volumeDelivery']['publication'],'requires_complete_multivolume_manifest')
            self.assertEqual(tables[1].rows.iterations,1)
        empty = [table('empty',0,actual=1)]; plan, outputs=prepare(empty)
        with self.assertRaisesRegex(AnalysisContractError,"多行"):
            run(empty,outputs,plan)

    def test_late_writer_failure_and_cancel_do_not_return_manifest(self):
        bad = [table("good",1),table("bad",1,make=lambda i:["bad",float('nan')])]
        plan, outputs=prepare(bad,max_tables=1)
        with self.assertRaises(AnalysisContractError):run(bad,outputs,plan,max_tables=1)
        self.assertGreater(len(outputs[0].xlsx.getvalue()),0)
        tables=[table("one",3)];plan,outputs=prepare(tables,max_rows=1,max_tables=1)
        def cancel(progress):
            if progress['volumeIndex']==2:raise RuntimeError('caller cancelled')
        with self.assertRaisesRegex(RuntimeError,'cancelled'):run(tables,outputs,plan,max_rows=1,max_tables=1,checkpoint=cancel)
        self.assertEqual(tables[0].rows.position,1)

    def test_malicious_text_stays_data_and_fragment_subtotals_are_labeled(self):
        malicious='</script><script>alert("x")</script>=HYPERLINK("https://invalid")'
        tables=[Table('safe','恶意文本检查','原始说明', (Column('name','文本'),Column('value','值','integer',True)),OnePass(2,lambda i:[malicious,i+1]),2)]
        plan,outputs=prepare(tables,max_tables=1,max_rows=1)
        proof=run(tables,outputs,plan,max_tables=1,max_rows=1)
        for output in outputs:
            html=output.html.getvalue().decode();document=ReportData(html).value
            self.assertNotIn('</script><script>alert',html)
            self.assertEqual(document['tables'][0]['rows'][0][0],malicious)
            self.assertIn('此片合计仅覆盖本片',document['tables'][0]['note'])
            with zipfile.ZipFile(output.xlsx) as archive:
                sheet=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
                self.assertEqual(sheet.find('.//{'+NS+'}c[@r="A4"]/{'+NS+'}is/{'+NS+'}t').text,malicious)
                self.assertIsNone(sheet.find('.//{'+NS+'}c[@r="A4"]/{'+NS+'}f'))
                self.assertFalse(any('externalLink' in name for name in archive.namelist()))
        self.assertEqual(proof['totalRows'],2)

    def test_forged_plan_and_wrong_binding_fail_before_reading_or_writing(self):
        for change in ('binding','offset','capacity','digest'):
            tables=[table('one',3)];plan,outputs=prepare(tables,max_rows=2)
            bad=deepcopy(plan)
            if change=='binding':bad['evidenceDigest']='b'*64
            elif change=='offset':bad['volumes'][0]['tables'][1]['rowOffset']=1
            elif change=='capacity':bad['capacity']['maxRows']=3
            else:bad['planDigest']='0'*64
            if change!='digest':bad['planDigest']=digest({k:v for k,v in bad.items() if k!='planDigest'})
            with self.assertRaises(AnalysisContractError):run(tables,outputs,bad,max_rows=2)
            self.assertEqual(tables[0].rows.iterations,0)
            self.assertEqual(outputs[0].html.getvalue(),b'')

    def test_streams_must_be_empty_distinct_and_owned_by_caller(self):
        tables=[table('one')];plan,outputs=prepare(tables)
        output=io.BytesIO()
        for pairs in ([VolumeStreams(output,output)],[VolumeStreams(io.BytesIO(b'old'),io.BytesIO())],[]):
            with self.assertRaises(AnalysisContractError):run(tables,pairs,plan)
        with tempfile.TemporaryFile('w+b') as x, tempfile.TemporaryFile('w+b') as h:
            proof=run(tables,[VolumeStreams(x,h)],plan)
            self.assertFalse(x.closed);self.assertFalse(h.closed)
            self.assertEqual(x.tell(),0);self.assertEqual(h.tell(),0)
            self.assertEqual(proof['status'],'complete')

    def test_byte_limit_rejects_entire_delivery_instead_of_fake_dynamic_split(self):
        tables=[table('one')];plan,outputs=prepare(tables)
        with self.assertRaisesRegex(AnalysisContractError,'字节超限'):
            run(tables,outputs,plan,max_file_bytes=100)
        self.assertEqual(tables[0].rows.iterations,0)
        tables=[table('one')];plan,outputs=prepare(tables)
        with self.assertRaises(AnalysisContractError):run(tables,outputs,plan,max_file_bytes=256*1024*1024+1)
        proof=run(tables,outputs,plan)
        self.assertEqual(proof['byteCapacity'],{'verified':True,'maxFileBytes':256*1024*1024,'dynamicByteSplitting':False})

    def test_late_volume_byte_overflow_does_not_publish_earlier_success(self):
        small=[table('first'),table('second')];plan,baseline=prepare(small,max_tables=1)
        run(small,baseline,plan,max_tables=1)
        limit=max(len(stream.getvalue()) for pair in baseline for stream in (pair.xlsx,pair.html))+200
        tables=[table('first'),table('second',make=lambda i:['X'*5000,1])];plan,outputs=prepare(tables,max_tables=1)
        with self.assertRaisesRegex(AnalysisContractError,'字节超限'):
            run(tables,outputs,plan,max_tables=1,max_file_bytes=limit)
        self.assertEqual(tables[0].rows.position,1)
        with zipfile.ZipFile(outputs[0].xlsx) as archive:
            self.assertIsNone(archive.testzip())
        self.assertGreater(len(outputs[1].html.getvalue()),0)

    def test_writer_forged_fragment_digest_is_rejected(self):
        tables=[table('one')];plan,outputs=prepare(tables)
        original=volume_files.report_files.write_pair
        def tamper(*args,**kwargs):
            result=original(*args,**kwargs)
            result['tables'][0]['rowDigest']='0'*64
            return result
        with patch.object(volume_files.report_files,'write_pair',side_effect=tamper),self.assertRaisesRegex(AnalysisContractError,'同源行摘要'):
            run(tables,outputs,plan)

    def test_budget_binding_and_reserved_metadata_cannot_be_replaced(self):
        p,b=budget_fixture();budget=payload(calculate(p,b),'wrong-report')
        tables=[table('one')];plan,outputs=prepare(tables,native_budget_sheets=3)
        with self.assertRaisesRegex(AnalysisContractError,'预算试算与多卷报告身份'):
            run(tables,outputs,plan,excel_budget=budget)
        self.assertEqual(tables[0].rows.iterations,0)
        tables=[table('one')];plan,outputs=prepare(tables)
        with self.assertRaisesRegex(AnalysisContractError,'保留绑定字段'):
            render(tables,outputs,plan=plan,report_id='report-volume-test',evidence_digest='a'*64,renderer_version=4,title='test',metadata={'volumeDelivery':{}})

    def test_mutating_output_and_table_lists_cannot_swap_frozen_references(self):
        tables=[table('first'),table('second')];plan,outputs=prepare(tables,max_tables=1)
        originals=tuple(outputs)
        def mutate(progress):
            if progress['stage']=='preparing_volume' and progress['volumeIndex']==1:
                outputs[1]=outputs[0]
                tables[1]=table('replacement')
        proof=run(tables,outputs,plan,max_tables=1,checkpoint=mutate)
        self.assertEqual([t['key'] for t in proof['tables']],['first','second'])
        for output,volume in zip(originals,proof['volumes']):
            self.assertEqual(hashlib.sha256(output.html.getvalue()).hexdigest(),volume['files']['html']['sha256'])
            self.assertEqual(hashlib.sha256(output.xlsx.getvalue()).hexdigest(),volume['files']['xlsx']['sha256'])

    def test_rows_cannot_mutate_budget_or_metadata_between_excel_and_html(self):
        p,b=budget_fixture();budget=payload(calculate(p,b),'report-volume-test')
        old_digest=budget['planDigest'];p['totalBudgetCents']=20000
        other=payload(calculate(p,b),'report-volume-test')
        metadata={'nested':{'note':'original'}}
        def row(i):
            budget.clear();budget.update(other);metadata['nested']['note']='replaced'
            return ['row',1]
        tables=[table('first',make=row)];plan,outputs=prepare(tables,native_budget_sheets=3)
        proof=render(tables,outputs,plan=plan,report_id='report-volume-test',evidence_digest='a'*64,renderer_version=4,
                     title='test',metadata=metadata,offline_budget=budget,excel_budget=budget)
        document=outputs[0].html.getvalue().decode()
        html_budget=json.loads(re.search(r'id="budget-data">(.*?)</script>',document,re.S).group(1))
        self.assertEqual(html_budget['planDigest'],old_digest)
        self.assertEqual(proof['volumes'][0]['budgetCalculator']['planDigest'],old_digest)
        self.assertEqual(proof['budgetPlanDigest'],old_digest)
        self.assertEqual(ReportData(document).value['metadata']['nested']['note'],'original')
        self.assertNotEqual(budget['planDigest'],old_digest)  # The caller really mutated its alias.

    def test_later_callback_overwriting_an_earlier_file_refuses_complete_manifest(self):
        for stage in ('preparing_volume','verifying_complete_delivery'):
            tables=[table('first'),table('second')];plan,outputs=prepare(tables,max_tables=1)
            def corrupt(progress):
                if progress['stage']==stage and (stage=='verifying_complete_delivery' or progress['volumeIndex']==2):
                    outputs[0].html.seek(0);outputs[0].html.write(b'CORRUPT')
            with self.subTest(stage=stage),self.assertRaisesRegex(AnalysisContractError,'先前卷文件'):
                run(tables,outputs,plan,max_tables=1,checkpoint=corrupt)

    def test_offline_budget_must_recompute_and_match_its_claimed_digest(self):
        p,b=budget_fixture();valid=payload(calculate(p,b),'report-volume-test')
        variants=[{'reportId':'report-volume-test'}, {**valid,'planDigest':'0'*64}, {**valid,'expected':{}}, {**valid,'schemaVersion':'fake'}]
        for budget in variants:
            tables=[table('first')];plan,outputs=prepare(tables)
            with self.assertRaises(AnalysisContractError):run(tables,outputs,plan,offline_budget=budget)
            self.assertEqual(tables[0].rows.iterations,0)
            self.assertEqual(outputs[0].html.getvalue(),b'')

    def test_bounded_context_and_mutable_column_declarations(self):
        for metadata in ({'huge':'x'*(2*1024*1024+1)}, {'object':object()}):
            tables=[table('one')];plan,outputs=prepare(tables)
            with self.assertRaises(AnalysisContractError):
                render(tables,outputs,plan=plan,report_id='report-volume-test',evidence_digest='a'*64,renderer_version=4,title='test',metadata=metadata)
            self.assertEqual(tables[0].rows.iterations,0)
        refs=[0,1];columns=[Column('a','a','integer'),Column('b','b','integer'),Column('ratio','ratio','ratio',ratio_of=refs)]
        def row(i):
            refs.reverse();columns.clear()
            return [1,2,.5]
        tables=[Table('one','ratios','',columns,OnePass(1,row),1)];plan,outputs=prepare(tables)
        proof=run(tables,outputs,plan)
        self.assertEqual(proof['totalRows'],1)
        with zipfile.ZipFile(outputs[0].xlsx) as archive:
            self.assertIn('A4/B4',archive.read('xl/worksheets/sheet1.xml').decode())

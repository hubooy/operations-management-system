"""Synthetic stored screening, independent five-job receipts and actual files.

These fixtures author model answers and receipts; they do not claim a provider
was run. Fact readers, screening publication, content and renderers are real.
"""
from copy import deepcopy
import base64
import hashlib
import io
import json
import zipfile
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from django.test import TransactionTestCase, override_settings
from django.db import connection

from business_analysis import screening_package, volume_delivery
from business_analysis.volume_files import VolumeStreams
from . import business_screening_export as service, business_export as export
from . import business_files as files, business_volume_files as volumes, business_evidence, models as m
from . import business_screening_packages as packages, business_screening_content as content
from . import test_business_screening_content as fixtures
from .business_sealed import Reader
from .policy import AiError, canonical, mutation


class ScreeningExportPureTests(TestCase):
    def test_private_file_capsule_and_transaction_gate(self):
        with self.assertRaises(AiError):files._ScreeningFileBinding(None,{})
        capsule=files._ScreeningFileBinding(files._SCREENING_FILE_TOKEN,{"nested":{"value":1}})
        alias=capsule.value;alias['nested']['value']=2
        self.assertEqual(capsule.value['nested']['value'],1)
        object.__setattr__(capsule,'_raw','{}')
        with self.assertRaises(AiError):_ = capsule.value
        report=SimpleNamespace(snapshot_json=canonical({'executionProfile':'business-agent-screening-reference-v1'}))
        with patch.object(files,'connection',SimpleNamespace(in_atomic_block=True)),patch.object(files,'authorize_owner'):
            with self.assertRaises(AiError):files.binding(report,None,True,renderer_version=4)
            with self.assertRaises(AiError):files._prepare_screening_binding(report,None,True)

    def test_long_unicode_nested_record_is_lossless_and_never_exceeds_four_columns(self):
        records=[{"text":"中😀\""*9000,"wide":{str(n):n for n in range(200)}},{}]
        with export.TableSpool() as spool:
            spool.add("proof","proof","proof",service._chunks(records))
            table=spool.tables[0]
            self.assertEqual(len(table.columns),4)
            rows=[dict(zip([c.key for c in table.columns],row)) for row in table.rows]
        actual=[]
        for index in range(2):
            parts=[r for r in rows if r['/recordIndex']==index]
            self.assertEqual([r['/fragmentIndex'] for r in parts],list(range(1,len(parts)+1)))
            self.assertTrue(all(r['/fragmentCount']==len(parts) for r in parts))
            self.assertTrue(all(len(r['/canonicalJson'].encode('utf-16-le'))//2<=14000 for r in parts))
            actual.append(json.loads(''.join(r['/canonicalJson'] for r in parts)))
        self.assertEqual(actual,records)


@override_settings(DJANGO_PROCESS_ROLE="development",DJANGO_ENVIRONMENT="test")
class ScreeningExportTests(TransactionTestCase):
    user=fixtures.ScreeningContentTests.user
    call=fixtures.ScreeningContentTests.call
    collect_body=fixtures.ScreeningContentTests.collect_body
    bundle=fixtures.ScreeningContentTests.bundle
    input_for=fixtures.ScreeningContentTests.input_for
    insert=fixtures.ScreeningContentTests.insert
    seed=fixtures.ScreeningContentTests.seed
    setUp=fixtures.ScreeningContentTests.setUp
    screening_bundle=fixtures.ScreeningContentTests.screening_bundle
    insert_screening=fixtures.ScreeningContentTests.insert_screening
    create_complete=fixtures.ScreeningContentTests.create_complete

    def decoded_rows(self,table):
        columns=[column.key for column in table.columns]
        fragments={}
        for values in table.rows:
            row=dict(zip(columns,values));index=row['/recordIndex']
            group=fragments.setdefault(index,[])
            self.assertEqual(row['/fragmentIndex'],len(group)+1)
            group.append(row['/canonicalJson'])
        return [json.loads(''.join(fragments[i])) for i in range(len(fragments))]

    def test_all_four_combinations_keep_raw_native_mapped_and_complete_proof_appendices(self):
        for mapped,budget in ((False,False),(False,True),(True,False),(True,True)):
            with self.subTest(mapped=mapped,budget=budget):
                report,prepared=self.create_complete(mapped=mapped,budget=budget)
                _,package,_=next(entry for entry in prepared.packages._packages if entry[0]=='report')
                decoded=screening_package.decode_pages(package.pages())
                with export.package(report,self.admin,draft=True,renderer_version=4) as (metadata,tables,calculator):
                    indexed={table.key:table for table in tables}
                    self.assertEqual(metadata['screeningRef'],json.loads(prepared._storage_reference_json))
                    self.assertEqual(set(metadata['screeningPackageDigests']),set(screening_package.ROLES))
                    self.assertTrue(all(len(t.columns)<=160 for t in tables))
                    self.assertTrue(any(key.startswith('raw-') for key in indexed))
                    self.assertTrue(any(key.startswith('analysis-') for key in indexed))
                    self.assertEqual(any(key.startswith('mapped-analysis-') for key in indexed),mapped)
                    self.assertEqual(calculator is not None,budget)
                    self.assertEqual(self.decoded_rows(indexed['screening-candidates']),[c for g in decoded['candidates'] for c in g['items']])
                    for kind in ('family','requested','table','partition'):
                        self.assertEqual(self.decoded_rows(indexed['screening-'+kind]),[r['value'] for r in decoded['coverage'] if r['kind']==kind])
                    self.assertEqual(self.decoded_rows(indexed['screening-authority']),[decoded['authority']])

    def test_real_candidate_xlsx_multivolume_manifest_and_first_volume_budget(self):
        report,_=self.create_complete(mapped=True,budget=True,candidate=True)
        with export.prepare_volumes(report,self.admin,draft=True,max_tables=30) as prepared:
            self.assertGreater(prepared.plan['volumeCount'],1)
            outputs=[VolumeStreams(io.BytesIO(),io.BytesIO()) for _ in range(prepared.plan['volumeCount'])]
            full=export.build_volumes(prepared,outputs)
        root,raw=volume_delivery.make(full,binding_digest='b'*64,attempt=1,draft=True,max_tables=30)
        self.assertEqual(volume_delivery.verify_full(root,raw,binding_digest='b'*64,attempt=1,draft=True,
            report_id=report.id,evidence_digest=json.loads(report.snapshot_json)['sealedDigest'],max_tables=30),full)
        candidates=next(t for t in full['tables'] if t['key']=='screening-candidates')
        self.assertGreater(candidates['rowCount'],0)
        self.assertEqual(full['volumes'][0]['nativeBudgetSheets'],3)
        self.assertTrue(all(v['nativeBudgetSheets']==0 for v in full['volumes'][1:]))
        for stream in outputs:
            with zipfile.ZipFile(io.BytesIO(stream.xlsx.getvalue())) as archive:
                self.assertIsNone(archive.testzip())
                self.assertTrue(any(name.startswith('xl/worksheets/') for name in archive.namelist()))
        self.assertEqual(set(root),volume_delivery.ROOT_FIELDS)

    def forbid_locked_facts(self):
        original = Reader.pages
        def outside(reader, *args, **kwargs):
            self.assertFalse(connection.in_atomic_block, "sealed facts opened under file mutation")
            for page in original(reader, *args, **kwargs):
                self.assertFalse(connection.in_atomic_block, "sealed facts consumed under file mutation")
                yield page
        guard = patch.object(Reader, "pages", outside)
        guard.start(); self.addCleanup(guard.stop)

    def test_durable_resume_and_chunk_reads_use_live_roots_without_scanning(self):
        report,_=self.create_complete(mapped=True,budget=True)
        self.forbid_locked_facts()
        created=files.create(report.id,{'deliveryMode':'volumes','draft':True,
            'expectedPrincipalKey':business_evidence.principal_key(self.admin)},self.admin)['item']
        original=files.audit
        def fail_ready(row,principal,action):
            if action=='volumes_ready':raise RuntimeError('synthetic final publish failure')
            return original(row,principal,action)
        with patch.object(files,'audit',side_effect=fail_ready):
            first=files.tick()
        self.assertEqual(first['status'],'paused',first)
        row=files.get(created['id'],self.admin);self.assertNotEqual(row.manifest_json,'{}')
        count=m.AiBusinessVolumeChunk.objects.filter(run=row).count()
        files.control(row.id,{'expectedVersion':row.version,'action':'resume'},self.admin)
        with patch.object(export,'prepare_volumes',side_effect=AssertionError('staged resume must not render again')):
            result=files.tick()
        self.assertEqual(result['status'],'ready',result)
        row=files.get(row.id,self.admin);self.assertEqual(row.attempt,1)
        self.assertEqual(m.AiBusinessVolumeChunk.objects.filter(run=row).count(),count)
        compact=json.loads(row.manifest_json);chunks=[]
        with (patch.object(Reader,'pages',side_effect=AssertionError('chunk must not scan facts')),
                patch.object(packages,'prepare',side_effect=AssertionError('chunk must not rebuild role packages')),
                patch.object(content,'content',side_effect=AssertionError('chunk must not revalidate all jobs'))):
            for sequence in range(1,compact['manifestFile']['chunkCount']+1):
                chunks.append(base64.b64decode(volumes.chunk(row.id,'0','json',{'sequence':str(sequence)},self.admin)['base64']))
        raw=b''.join(chunks)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),compact['manifestFile']['sha256'])
        full=json.loads(raw);self.assertEqual(full['screeningRef'],service.binding(report,self.admin)['screeningRef'])
        changed=deepcopy(service.metadata(report,self.admin));changed['screeningRef']['resultDigest']='f'*64
        with patch.object(service,'metadata',return_value=changed),self.assertRaises(AiError):
            volumes._verify_staged(row,self.admin,lambda:None)
        other=self.user('screening-export-other@example.invalid','admin',None)
        with self.assertRaises(AiError):volumes.chunk(row.id,'0','json',{'sequence':'1'},other)

    def test_creation_rejects_actual_late_ledger_change_and_public_capsule(self):
        report,_=self.create_complete(mapped=True,budget=True)
        self.forbid_locked_facts()
        with self.assertRaises(AiError):files._check_screening_binding({},report,self.admin,True)
        original=files._prepare_screening_binding
        def changed(*args,**kwargs):
            prepared=original(*args,**kwargs)
            with mutation(self.admin):
                row=m.AiAgentProviderDispatches.objects.filter(job__workflow_run_id=report.workflow_id).first()
                row.request_digest='f'*64;row.save(update_fields=['request_digest'])
            return prepared
        with patch.object(files,'_prepare_screening_binding',side_effect=changed),self.assertRaises(AiError):
            files.create(report.id,{'deliveryMode':'volumes','draft':True,
                'expectedPrincipalKey':business_evidence.principal_key(self.admin)},self.admin)
        self.assertEqual(m.AiBusinessFileRun.objects.filter(report=report).count(),0)

    def test_final_publish_rejects_ledger_change_after_full_outside_validation(self):
        report,_=self.create_complete(mapped=True,budget=True)
        self.forbid_locked_facts()
        run_id=files.create(report.id,{'deliveryMode':'volumes','draft':True,
            'expectedPrincipalKey':business_evidence.principal_key(self.admin)},self.admin)['item']['id']
        original=files._prepare_screening_binding
        def changed(*args,**kwargs):
            prepared=original(*args,**kwargs)
            with mutation(self.admin):
                row=m.AiAgentProviderDispatches.objects.filter(job__workflow_run_id=report.workflow_id).first()
                row.request_digest='f'*64;row.save(update_fields=['request_digest'])
            return prepared
        with patch.object(files,'_prepare_screening_binding',side_effect=changed):result=files.tick()
        self.assertEqual(result['status'],'paused',result)
        row=files.get(run_id,self.admin)
        self.assertNotEqual(row.manifest_json,'{}')
        self.assertNotEqual(row.status,'ready')

    def activate_http_authority(self):
        from .control_models import AiWriteAuthority
        from django.utils import timezone
        with mutation(self.admin):
            AiWriteAuthority.objects.filter(pk=1).update(status='postgres',
                authority_epoch='00000000-0000-0000-0000-000000000001',cutover_id='screening-file-http-fixture',
                migration_verify_run_id='isolated-file-http-fixture',activated_at=timezone.now())

    def test_signed_http_creation_resume_and_exact_request_replay_are_outside_all_transactions(self):
        from .control_models import AiWriteReceipt
        report,_=self.create_complete(mapped=True,budget=True)
        self.activate_http_authority()
        self.forbid_locked_facts()
        path=f'/api/ai/reports/{report.id}/files'
        payload={'deliveryMode':'volumes','draft':True,
            'expectedPrincipalKey':business_evidence.principal_key(self.admin)}
        with override_settings(DJANGO_PROCESS_ROLE='ai_writer',AI_WRITE_AUTHORITY_EPOCH='00000000-0000-0000-0000-000000000001',
                AI_WRITE_CUTOVER_ID='screening-file-http-fixture'):
            result=self.call(path,payload,principal=self.admin,request_id='screen-files-http-create')
            self.assertEqual(result.status_code,200,result.content)
            run_id=result.json()['item']['id']
            self.assertEqual(AiWriteReceipt.objects.get(pk='screen-files-http-create').status,'completed')
            with patch.object(files,'_prepare_screening_binding',side_effect=AssertionError('HTTP replay cannot recompute')):
                replay=self.call(path,payload,principal=self.admin,request_id='screen-files-http-create')
            self.assertEqual(replay.status_code,200,replay.content)
            self.assertEqual(replay.headers.get('X-Teruisi-Write-Replay'),'1')
            self.assertEqual(replay.json(),result.json())
            row=files.get(run_id,self.admin)
            files.control(run_id,{'action':'pause','expectedVersion':row.version},self.admin)
            row=files.get(run_id,self.admin)
            control=f'/api/ai/business-files/{run_id}/control'
            resume={'action':'resume','expectedVersion':row.version}
            result=self.call(control,resume,principal=self.admin,request_id='screen-files-http-resume')
            self.assertEqual(result.status_code,200,result.content)
            self.assertEqual(result.json()['item']['status'],'queued')
            with patch.object(files,'_prepare_screening_binding',side_effect=AssertionError('HTTP replay cannot recompute')):
                replay=self.call(control,resume,principal=self.admin,request_id='screen-files-http-resume')
            self.assertEqual(replay.status_code,200,replay.content)
            self.assertEqual(replay.json(),result.json())

    def test_http_finish_failure_rolls_back_file_mutation_with_processing_receipt(self):
        from . import views
        from .control_models import AiWriteReceipt
        report,_=self.create_complete(mapped=True,budget=True)
        self.activate_http_authority()
        self.forbid_locked_facts()
        path=f'/api/ai/reports/{report.id}/files'
        payload={'deliveryMode':'volumes','draft':True,
            'expectedPrincipalKey':business_evidence.principal_key(self.admin)}
        original=views.finish
        def abort_after_finish(*args,**kwargs):
            self.assertTrue(connection.in_atomic_block)
            original(*args,**kwargs)
            raise AiError('synthetic receipt commit failure','conflict',409)
        with override_settings(DJANGO_PROCESS_ROLE='ai_writer',AI_WRITE_AUTHORITY_EPOCH='00000000-0000-0000-0000-000000000001',
                AI_WRITE_CUTOVER_ID='screening-file-http-fixture'):
            with patch.object(views,'finish',side_effect=abort_after_finish):
                result=self.call(path,payload,principal=self.admin,request_id='screen-files-create-rollback')
            self.assertEqual(result.status_code,409,result.content)
            self.assertEqual(m.AiBusinessFileRun.objects.filter(report=report).count(),0)
            self.assertEqual(AiWriteReceipt.objects.get(pk='screen-files-create-rollback').status,'processing')
            with patch.object(files,'_prepare_screening_binding',side_effect=AssertionError('uncertain request must not run again')):
                retry=self.call(path,payload,principal=self.admin,request_id='screen-files-create-rollback')
            self.assertEqual(retry.json()['code'],'request_pending')
            result=self.call(path,payload,principal=self.admin,request_id='screen-files-create-new')
            self.assertEqual(result.status_code,200,result.content)
            run_id=result.json()['item']['id'];row=files.get(run_id,self.admin)
            files.control(run_id,{'action':'pause','expectedVersion':row.version},self.admin)
            row=files.get(run_id,self.admin);version=row.version
            with patch.object(views,'finish',side_effect=abort_after_finish):
                result=self.call(f'/api/ai/business-files/{run_id}/control',{'action':'resume','expectedVersion':version},
                    principal=self.admin,request_id='screen-files-resume-rollback')
            self.assertEqual(result.status_code,409,result.content)
            row=files.get(run_id,self.admin)
            self.assertEqual((row.status,row.version),('paused',version))
            self.assertEqual(AiWriteReceipt.objects.get(pk='screen-files-resume-rollback').status,'processing')

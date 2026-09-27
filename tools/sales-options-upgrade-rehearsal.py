"""Synthetic sales 0009->0010 upgrade and independent restore; isolated only."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
import django
django.setup()
import psycopg
from psycopg import sql
from django.conf import settings
from django.apps import apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run-root',type=Path,required=True)
folder=parser.parse_args().run_root.resolve()
database=settings.DATABASES['default']
if (ROOT==Path(r'D:\运营管理系统') or settings.DJANGO_ENVIRONMENT!='test'
        or database['HOST']!='127.0.0.1' or str(database['PORT'])!=os.getenv('TERUISI_AI_REHEARSAL_PORT')
        or not 55440<=int(database['PORT'])<=55999 or database['NAME']!='teruisi_ai_rehearsal'
        or connection.vendor!='postgresql' or connection.introspection.table_names()
        or folder.parent!=ROOT/'.runtime'):
    raise RuntimeError('Requires a fresh isolated rehearsal database')
BIN=Path(r'D:\teruisi-runtime\django-sales\postgresql-17.11\bin')
new_tables={'sales_analysis_options','sales_analysis_options_state'}

def snapshot(db,tables):
    state={}
    for table in sorted(tables):
        state[table]=sorted(json.dumps(row[0],sort_keys=True,default=str,ensure_ascii=False)
            for row in db.execute(sql.SQL('SELECT row_to_json(t) FROM {} t').format(sql.Identifier(table))))
    return hashlib.sha256(json.dumps(state,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def dbconnect(name):
    return psycopg.connect(host=database['HOST'],port=database['PORT'],dbname=name,
        user=database['USER'],password=database['PASSWORD'])

spec=importlib.util.spec_from_file_location('backup_contract',ROOT/'tools/postgres-consistent-backup.py')
backup=importlib.util.module_from_spec(spec);spec.loader.exec_module(backup)
executor=MigrationExecutor(connection)
targets=[node for node in executor.loader.graph.leaf_nodes() if node[0] != 'sales']
targets.append(('sales','0009_postgres_raw_upload_payload'))
executor.migrate(targets)
old_tables={t for t in connection.introspection.table_names() if t.startswith('sales_')}
assert not old_tables & new_tables
from sales.models import SalesOrderLine,SalesDataRevision,SalesWriteAuthority
from access_control.models import AppUser,AccessRole
from sales.auth import Principal
from sales.tests.factories import make_line
from sales.tests.cutover_fixtures import install_writer_runtime_guard
now=timezone.now();epoch=uuid.UUID('11111111-1111-4111-8111-111111111111')
# This is a fresh synthetic database. The complete backup collector also checks
# every installed domain's control state, even though this rehearsal only changes
# sales. Retain inactive migration seeds and fill missing empty-domain controls;
# do not weaken the production collector or activate unrelated owning services.
for app,model,domain in (
    ('sales','SalesDataRevision','sales'),('sales','SalesDataRevision','erp'),
    ('netshop','NetshopDataRevision','netshop'),('finance','FinanceDataRevision','finance'),
    ('market','MarketDataRevision','market'),
    ('products','ProductDataRevision','products'),('inventory','InventoryDataRevision','inventory'),
    ('workflow','WorkflowDataRevision','workflow'),
    ('customer_service','CustomerServiceDataRevision','customer-service'),
    ('access_control','AccessControlDataRevision','access-control'),
    ('ai_assistant','AiDataRevision','ai-assistant'),
):
    apps.get_model(app,model).objects.get_or_create(domain=domain,
        defaults={'revision':1,'source_digest':'0'*64})
for app,model,status in (
    ('erp_reference','ErpReferenceWriteAuthority','d1'),
    ('netshop','NetshopWriteAuthority','d1'),('finance','FinanceWriteAuthority','d1'),
    ('market','MarketWriteAuthority','d1'),
    ('products','ProductWriteAuthority','d1'),('inventory','InventoryWriteAuthority','d1'),
    ('workflow','WorkflowWriteAuthority','disabled'),
    ('workflow','WorkflowOperationsWriteAuthority','disabled'),
    ('customer_service','CustomerServiceWriteAuthority','d1'),
    ('access_control','AccessControlWriteAuthority','d1'),
    ('ai_assistant','AiWriteAuthority','d1'),
):
    control,_=apps.get_model(app,model).objects.get_or_create(id=1,defaults={'status':status})
    assert control.status==status and control.authority_epoch is None and not control.cutover_id
apps.get_model('market','MarketDataRevision').objects.filter(domain='market').update(revision=1,source_digest='0'*64)
# The full backup contract requires an active sales fence. This synthetic fence
# is test-only; subsequent rows and runtime evidence are synthetic fixtures.
apps.get_model('sales','SalesWriteAuthority').objects.update_or_create(id=1,defaults={
    'status':'active','authority_epoch':epoch,'cutover_id':'sales-options-upgrade-sales','activated_at':now})
role,_=AccessRole.objects.get_or_create(code='admin',defaults={'rank':40,'label':'Admin'})
AppUser.objects.create(email='sales-upgrade@example.invalid',display_name='Synthetic',role=role,status='active',
    scope=None,version=1,created_at=now,updated_at=now)
# Build real current-fact projections before adding the directory tables.
install_writer_runtime_guard('sales-options-upgrade-sales')
SalesOrderLine.objects.bulk_create([make_line(1,'options-upgrade-one'),make_line(2,'options-upgrade-two')])
with dbconnect(database['NAME']) as db:
    before=snapshot(db,old_tables)
    old_evidence=backup.collect_evidence(db,database['NAME'],database['USER'])
target=[('sales','0010_analysis_options')]
MigrationExecutor(connection).migrate(target)
with dbconnect(database['NAME']) as db: assert snapshot(db,old_tables)==before
assert MigrationExecutor(connection).migration_plan(target)==[]
from sales import analysis_options_projection as projection,analysis_options
from sales.models import SalesAnalysisOption,SalesAnalysisOptionsState
assert SalesAnalysisOption.objects.count()==0
assert SalesAnalysisOptionsState.objects.get(id=1).status=='not_ready'
MigrationExecutor(connection).migrate([('sales','0009_postgres_raw_upload_payload')])
assert not new_tables & set(connection.introspection.table_names())
MigrationExecutor(connection).migrate(target)
with dbconnect(database['NAME']) as db: assert snapshot(db,old_tables)==before
principal=Principal('sales-upgrade@example.invalid','Synthetic','admin',None)
from django.test import override_settings
with override_settings(SALES_WRITE_AUTHORITY_EPOCH=str(epoch),SALES_WRITE_CUTOVER_ID='sales-options-upgrade-sales'):
    projection.publish_rebuild(projection.prepare_rebuild(principal),principal)
    page=analysis_options.read_page(principal,{})
assert len(page['items'])==1 and page['authorityVerified']
assert page['items'][0]['dateMetadata']['coverageVerified'] is False
try:
    MigrationExecutor(connection).migrate([('sales','0009_postgres_raw_upload_payload')])
except RuntimeError as error:
    assert 'initialized' in str(error)
else: raise AssertionError('Populated options rollback must fail')
assert MigrationExecutor(connection).migration_plan(target)==[]
with dbconnect(database['NAME']) as db:
    complete=snapshot(db,old_tables|new_tables)
    evidence=backup.collect_evidence(db,database['NAME'],database['USER'])
assert new_tables<=set(evidence['tables']) and not new_tables & set(old_evidence['tables'])
dump=folder/'sales-options.dump'
env={**os.environ,'PGHOST':str(database['HOST']),'PGPORT':str(database['PORT']),
    'PGDATABASE':str(database['NAME']),'PGUSER':str(database['USER']),
    'PGPASSWORD':str(database['PASSWORD'])}
def native(args):
    result=subprocess.run([str(a) for a in args],env=env,capture_output=True,timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if result.returncode:
        (folder/'sales-options-native-error.log').write_bytes(result.stderr)
        raise RuntimeError('Isolated native operation failed; see local error log')
native([BIN/'pg_dump.exe','--format=custom','--no-owner','--no-acl','--file',dump])
restore_name='sales_options_restore'
with psycopg.connect(host=database['HOST'],port=database['PORT'],dbname='postgres',
        user=database['USER'],password=database['PASSWORD'],autocommit=True) as admin:
    admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(restore_name)))
native([BIN/'pg_restore.exe','--no-owner','--no-acl','--exit-on-error','--dbname',restore_name,dump])
with dbconnect(restore_name) as restored:
    assert snapshot(restored,old_tables|new_tables)==complete
    restored_evidence=backup.collect_evidence(restored,restore_name,database['USER'])
    assert restored_evidence['tables']==evidence['tables']
connection.close()
connection.settings_dict['NAME']=restore_name
try:
    with override_settings(SALES_WRITE_AUTHORITY_EPOCH=str(epoch),SALES_WRITE_CUTOVER_ID='sales-options-upgrade-sales'):
        restored_page=analysis_options.read_page(principal,{})
    assert restored_page==page
finally:
    connection.close()
    connection.settings_dict['NAME']='teruisi_ai_rehearsal'
print(json.dumps({'status':'passed','upgrade':'sales.0009->0010','oldSalesTables':len(old_tables),
    'oldRowsDigestPreserved':before,'newTables':sorted(new_tables),'historicalAutoInitialization':False,
    'publishedIdentities':len(page['items']),'authorityVerified':True,'coverageVerified':False,
    'secondApplyNoop':True,'populatedRollbackDenied':True,'oldBackupStillAccepted':True,
    'independentRestoreDigest':complete,'backupTableEvidenceEqual':True,'restoredOwningPageEqual':True,'productionWrites':False}))

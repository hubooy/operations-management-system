"""Synthetic market 0005->0006 upgrade and independent restore; isolated only."""
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
new_tables={'market_analysis_options','market_analysis_options_state'}
facet_indexes={
    'mkt_facet_scope_idx', 'mkt_facet_dimension_idx',
    'mkt_facet_operation_idx', 'mkt_facet_subcategory_idx',
}

def snapshot(db,tables):
    state={}
    for table in sorted(tables):
        state[table]=sorted(json.dumps(row[0],sort_keys=True,default=str,ensure_ascii=False)
            for row in db.execute(sql.SQL('SELECT row_to_json(t) FROM {} t').format(sql.Identifier(table))))
    return hashlib.sha256(json.dumps(state,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def dbconnect(name):
    return psycopg.connect(host=database['HOST'],port=database['PORT'],dbname=name,
        user=database['USER'],password=database['PASSWORD'])

def facet_index_definitions(db):
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT indexname,indexdef FROM pg_catalog.pg_indexes "
            "WHERE schemaname='public' AND tablename='market_ranking_entries' "
            "AND indexname = ANY(%s)", (list(facet_indexes),))
        definitions=dict(cursor.fetchall())
        if set(definitions)!=facet_indexes:
            raise AssertionError('Adopted market facet indexes are missing')
        backup._require_market_facet_indexes(cursor)
    return definitions

spec=importlib.util.spec_from_file_location('backup_contract',ROOT/'tools/postgres-consistent-backup.py')
backup=importlib.util.module_from_spec(spec);spec.loader.exec_module(backup)
executor=MigrationExecutor(connection)
targets=[node for node in executor.loader.graph.leaf_nodes() if node[0] not in {'market','ai_assistant'}]
targets.extend([('market','0005_filter_facet_indexes'),('ai_assistant','0024_business_screening_runtime')])
executor.migrate(targets)
old_tables={t for t in connection.introspection.table_names() if t.startswith('market_')}
assert not old_tables & new_tables
from market.models import MarketImportBatch,MarketDataRevision,MarketWriteAuthority
from access_control.models import AppUser,AccessRole
from sales.auth import Principal
from market.revisions import canonical_json
now=timezone.now();epoch=uuid.UUID('11111111-1111-4111-8111-111111111111')
# This is a fresh synthetic database. The complete backup collector also checks
# every installed domain's control state, even though this rehearsal only changes
# market. Retain inactive migration seeds and fill missing empty-domain controls;
# do not weaken the production collector or activate unrelated owning services.
for app,model,domain in (
    ('sales','SalesDataRevision','sales'),('sales','SalesDataRevision','erp'),
    ('netshop','NetshopDataRevision','netshop'),('finance','FinanceDataRevision','finance'),
    ('products','ProductDataRevision','products'),('inventory','InventoryDataRevision','inventory'),
    ('workflow','WorkflowDataRevision','workflow'),
    ('customer_service','CustomerServiceDataRevision','customer-service'),
    ('access_control','AccessControlDataRevision','access-control'),
    ('ai_assistant','AiDataRevision','ai-assistant'),
):
    apps.get_model(app,model).objects.get_or_create(domain=domain,
        defaults={'revision':0,'source_digest':'0'*64})
for app,model,status in (
    ('erp_reference','ErpReferenceWriteAuthority','d1'),
    ('netshop','NetshopWriteAuthority','d1'),('finance','FinanceWriteAuthority','d1'),
    ('products','ProductWriteAuthority','d1'),('inventory','InventoryWriteAuthority','d1'),
    ('workflow','WorkflowWriteAuthority','disabled'),
    ('workflow','WorkflowOperationsWriteAuthority','disabled'),
    ('customer_service','CustomerServiceWriteAuthority','d1'),
    ('access_control','AccessControlWriteAuthority','d1'),
    ('ai_assistant','AiWriteAuthority','d1'),
):
    control,_=apps.get_model(app,model).objects.get_or_create(id=1,defaults={'status':status})
    assert control.status==status and control.authority_epoch is None and not control.cutover_id
# The full backup contract requires an active sales fence. This synthetic fence
# does not import sales/ERP facts or grant the current process writer authority.
apps.get_model('sales','SalesWriteAuthority').objects.update_or_create(id=1,defaults={
    'status':'active','authority_epoch':epoch,'cutover_id':'market-options-upgrade-sales','activated_at':now})
role,_=AccessRole.objects.get_or_create(code='admin',defaults={'rank':40,'label':'Admin'})
AppUser.objects.create(email='market-upgrade@example.invalid',display_name='Synthetic',role=role,status='active',
    scope=None,version=1,created_at=now,updated_at=now)
MarketWriteAuthority.objects.update_or_create(id=1,defaults=dict(status='postgres',authority_epoch=epoch,
    cutover_id='market-options-upgrade',migration_verify_run_id='market-'+'5'*24,activated_at=now))
MarketDataRevision.objects.update_or_create(domain='market',defaults=dict(revision=1,source_digest='a'*64))
# Populate an actual normalized successful historical batch using the old model
# state; no new projection or current import hook is executed before migration.
from market.tests.factories import market_row,prepared_payload
from market.import_service import validate_import_payload
payload=prepared_payload(market_row(periodStart='2026-09-01',periodEnd='2026-09-01'))
prepared=validate_import_payload(payload)
oldapps=executor.loader.project_state(targets).apps
fields={f.name for f in oldapps.get_model('market','MarketImportBatch')._meta.fields}
# Exact fixture construction follows the model's required existing fields.
values={'id':'synthetic-options-upgrade','source_type':payload['sourceType'],'status':'completed',
    'file_name':'synthetic.xlsx','file_size_bytes':1,'raw_file_hash':'1'*64,'content_hash':'2'*64,
    'scope_json':prepared['scope'],'row_count':1,'inserted_count':1,
    'actor_email':'market-upgrade@example.invalid','created_at':now,'completed_at':now}
oldapps.get_model('market','MarketImportBatch').objects.create(**{k:v for k,v in values.items() if k in fields})
with dbconnect(database['NAME']) as db:
    before=snapshot(db,old_tables)
    before_facet_indexes=facet_index_definitions(db)
    old_evidence=backup.collect_evidence(db,database['NAME'],database['USER'])
target=[('market','0006_analysis_options')]
MigrationExecutor(connection).migrate(target)
with dbconnect(database['NAME']) as db:
    assert snapshot(db,old_tables)==before
    assert facet_index_definitions(db)==before_facet_indexes
assert MigrationExecutor(connection).migration_plan(target)==[]
from market import analysis_options_projection as projection,analysis_options
from market.models import MarketAnalysisOption,MarketAnalysisOptionsState
assert MarketAnalysisOption.objects.count()==0
assert MarketAnalysisOptionsState.objects.get(id=1).status=='not_ready'
principal=Principal('market-upgrade@example.invalid','Synthetic','admin',None)
from django.test import override_settings
with override_settings(MARKET_WRITE_AUTHORITY_EPOCH=str(epoch),MARKET_WRITE_CUTOVER_ID='market-options-upgrade'):
    projection.publish_rebuild(projection.prepare_rebuild(principal),principal)
    page=analysis_options.read_page(principal,{})
assert len(page['items'])==1 and page['authorityVerified']
assert page['items'][0]['dateMetadata']['coverageVerified'] is False
try:
    MigrationExecutor(connection).migrate([('market','0005_filter_facet_indexes')])
except RuntimeError as error:
    assert 'initialized' in str(error)
else: raise AssertionError('Populated options rollback must fail')
assert MigrationExecutor(connection).migration_plan(target)==[]
with dbconnect(database['NAME']) as db:
    complete=snapshot(db,old_tables|new_tables)
    evidence=backup.collect_evidence(db,database['NAME'],database['USER'])
assert new_tables<=set(evidence['tables']) and not new_tables & set(old_evidence['tables'])
dump=folder/'market-options.dump'
env={**os.environ,'PGHOST':str(database['HOST']),'PGPORT':str(database['PORT']),
    'PGDATABASE':str(database['NAME']),'PGUSER':str(database['USER']),
    'PGPASSWORD':str(database['PASSWORD'])}
def native(args):
    result=subprocess.run([str(a) for a in args],env=env,capture_output=True,timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if result.returncode:
        (folder/'market-options-native-error.log').write_bytes(result.stderr)
        raise RuntimeError('Isolated native operation failed; see local error log')
native([BIN/'pg_dump.exe','--format=custom','--no-owner','--no-acl','--file',dump])
restore_name='market_options_restore'
with psycopg.connect(host=database['HOST'],port=database['PORT'],dbname='postgres',
        user=database['USER'],password=database['PASSWORD'],autocommit=True) as admin:
    admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(restore_name)))
native([BIN/'pg_restore.exe','--no-owner','--no-acl','--exit-on-error','--dbname',restore_name,dump])
with dbconnect(restore_name) as restored:
    assert snapshot(restored,old_tables|new_tables)==complete
    assert facet_index_definitions(restored)==before_facet_indexes
    restored_evidence=backup.collect_evidence(restored,restore_name,database['USER'])
    assert restored_evidence['tables']==evidence['tables']
connection.close()
connection.settings_dict['NAME']=restore_name
try:
    with override_settings(MARKET_WRITE_AUTHORITY_EPOCH=str(epoch),MARKET_WRITE_CUTOVER_ID='market-options-upgrade'):
        restored_page=analysis_options.read_page(principal,{})
    assert restored_page==page
finally:
    connection.close()
    connection.settings_dict['NAME']='teruisi_ai_rehearsal'
print(json.dumps({'status':'passed','upgrade':'market.0005->0006','oldMarketTables':len(old_tables),
    'oldRowsDigestPreserved':before,'newTables':sorted(new_tables),'historicalAutoInitialization':False,
    'adoptedFacetIndexesPreserved':sorted(facet_indexes),
    'publishedIdentities':len(page['items']),'authorityVerified':True,'coverageVerified':False,
    'secondApplyNoop':True,'populatedRollbackDenied':True,'oldBackupStillAccepted':True,
    'independentRestoreDigest':complete,'backupTableEvidenceEqual':True,'restoredOwningPageEqual':True,'productionWrites':False}))

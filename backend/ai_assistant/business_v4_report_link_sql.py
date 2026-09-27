"""Closed 0071 SQL contract: create-time v2 report to sealed-v4 identity.

No application caller is registered. These functions pin *identity*, not the
application HMAC, business authority, Agent reads, renderer or download.
The v4 parent/seal digests hash canonical JSON *string* values in Python;
SQL must compare pinned stored digests, not substitute SHA256(raw text).
"""

INTENTS = "public.protected_business_v4_report_link_intents"
LINKS = "public.protected_business_v4_report_source_links"
ISSUE = "public.ai_v4_issue_report_link_intent(text,text,text,text,text,text)"
CREATE_REPORT = "public.ai_v4_create_report_from_link_intent(text,text,text,text,text,text)"
READ = "public.ai_v4_read_report_source_link(text,text,bigint)"
BINDINGS = "public.ai_v4_report_source_bindings(text)"
WRITER = "teruisi_ai_writer"
READER = "teruisi_ai_reader"


ROW_GUARD = """CREATE FUNCTION public.ai_v4_report_link_row_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  IF TG_OP IS DISTINCT FROM 'INSERT' THEN RAISE EXCEPTION 'ai_v4_report_link_immutable'; END IF;
  IF session_user IS DISTINCT FROM 'teruisi_ai_writer'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid=TG_RELID))
  THEN RAISE EXCEPTION 'ai_v4_report_link_direct_write_denied'; END IF;
  RETURN NEW;
END $$"""


BINDINGS_SQL = """CREATE FUNCTION public.ai_v4_report_source_bindings(selected_run text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_v4_runs%ROWTYPE;
  seal public.ai_business_v4_seals%ROWTYPE;
  source public.ai_business_v4_sources%ROWTYPE;
  plan jsonb; body jsonb; chosen jsonb; claimed jsonb; query jsonb;
  origin_start text; origin_end text; shop text; windows text[]:=ARRAY[]::text[];
  source_rows jsonb:='[]'::jsonb; count_rows integer:=0;
  current_netshop text; current_finance text;
BEGIN
  SELECT * INTO parent FROM public.ai_business_v4_runs WHERE id=selected_run;
  SELECT * INTO seal FROM public.ai_business_v4_seals WHERE run_id=selected_run;
  IF parent.id IS NULL OR seal.run_id IS NULL OR parent.status IS DISTINCT FROM 'sealed'
     OR parent.collection_status IS DISTINCT FROM 'manual' OR parent.scope_json IS DISTINCT FROM 'null'
     OR parent.version IS DISTINCT FROM seal.evidence_version
     OR parent.plan_digest !~ '^[0-9a-f]{64}$'
     OR seal.body_digest !~ '^[0-9a-f]{64}$'
     OR seal.body_mac !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_v4_report_link_seal_invalid'; END IF;
  plan:=parent.plan_json::jsonb; body:=seal.body_json::jsonb;
  IF plan->>'schemaVersion' IS DISTINCT FROM 'business-evidence-v4-capacity-plan-v1'
     OR plan->'runCapacitySupported' IS DISTINCT FROM 'true'::jsonb
     OR plan->'reportGenerationSupported' IS DISTINCT FROM 'false'::jsonb
     OR plan->'modelDispatchSupported' IS DISTINCT FROM 'false'::jsonb
     OR jsonb_array_length(plan->'sourcePlans') IS DISTINCT FROM 4
     OR plan->'analysisRequest'->'requestedWindows' IS DISTINCT FROM  '["current","previous","yearAgo"]'::jsonb
     OR body->>'runId' IS DISTINCT FROM parent.id OR body->>'evidenceVersion' IS DISTINCT FROM parent.version::text
     OR body->>'planDigest' IS DISTINCT FROM parent.plan_digest
     OR body->'reportGenerationSupported' IS DISTINCT FROM 'false'::jsonb
     OR jsonb_array_length(body->'sources') IS DISTINCT FROM 4
  THEN RAISE EXCEPTION 'ai_v4_report_link_plan_invalid'; END IF;
  SELECT revision::text||':'||left(source_digest,12) INTO current_netshop
    FROM public.netshop_data_revisions WHERE domain='netshop' FOR SHARE;
  SELECT revision::text||':'||source_digest INTO current_finance
    FROM public.finance_data_revisions WHERE domain='finance' FOR SHARE;
  IF current_netshop IS NULL OR current_finance IS NULL THEN
    RAISE EXCEPTION 'ai_v4_report_link_revision_unknown'; END IF;
  FOR source IN SELECT * FROM public.ai_business_v4_sources
      WHERE run_id=parent.id ORDER BY ordinal LOOP
    count_rows:=count_rows+1;
    IF count_rows>4 OR source.ordinal IS DISTINCT FROM count_rows
       OR NOT source.finished OR source.version IS DISTINCT FROM source.page_count+1
       OR source.page_count<1 OR source.page_count>16384
       OR source.source_ref IS NULL OR source.source_ref !~ '^[0-9a-f]{64}$'
       OR source.source_revision IS NULL
       OR source.query_digest IS DISTINCT FROM encode(sha256(convert_to(source.query_json,'UTF8')),'hex')
    THEN RAISE EXCEPTION 'ai_v4_report_link_source_invalid'; END IF;
    chosen:=plan->'sourcePlans'->(count_rows-1);
    claimed:=body->'sources'->(count_rows-1);
    query:=source.query_json::jsonb;
    IF chosen->>'sourceKey' IS DISTINCT FROM source.source_key
       OR chosen->>'domain' IS DISTINCT FROM source.domain
       OR chosen->>'queryDigest' IS DISTINCT FROM source.query_digest
       OR chosen->>'sourceIdentityDigest' IS DISTINCT FROM source.source_identity_digest
       OR chosen->'query' IS DISTINCT FROM query
       OR claimed->>'sourceKey' IS DISTINCT FROM source.source_key
       OR claimed->>'domain' IS DISTINCT FROM source.domain
       OR claimed->>'queryDigest' IS DISTINCT FROM source.query_digest
       OR claimed->>'sourceRef' IS DISTINCT FROM source.source_ref
       OR claimed->>'sourceRevision' IS DISTINCT FROM source.source_revision
       OR claimed->>'sourceVersion' IS DISTINCT FROM source.version::text
       OR claimed->>'pageCount' IS DISTINCT FROM source.page_count::text
       OR claimed->>'rowCount' IS DISTINCT FROM source.row_count::text
       OR claimed->>'storedBytes' IS DISTINCT FROM source.stored_bytes::text
       OR claimed->>'liveRevision' IS DISTINCT FROM source.source_revision
       OR claimed->>'revisionFreshness' IS DISTINCT FROM 'current_revision'
    THEN RAISE EXCEPTION 'ai_v4_report_link_source_identity_drift'; END IF;
    IF source.domain='netshop' THEN
      IF source.temporal_role IS DISTINCT FROM 'daily_fact'
         OR source.source_revision IS DISTINCT FROM current_netshop
         OR query->>'platform' IS DISTINCT FROM '京东' OR query->>'dataset' IS DISTINCT FROM 'promotion'
         OR query->>'window' IS NULL
         OR query->>'window' NOT IN ('current','previous','yearAgo')
         OR query->>'shop' IS NULL OR query->>'shop'=''
         OR query->>'startDate' IS NULL OR query->>'endDate' IS NULL
         OR (query->>'startDate') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
         OR (query->>'endDate') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
      THEN RAISE EXCEPTION 'ai_v4_report_link_promotion_invalid'; END IF;
      IF origin_start IS NULL THEN
        origin_start:=query->>'startDate'; origin_end:=query->>'endDate';
        shop:=query->>'shop';
      ELSIF (origin_start,origin_end,shop) IS DISTINCT FROM
            (query->>'startDate',query->>'endDate',query->>'shop') THEN
        RAISE EXCEPTION 'ai_v4_report_link_cross_shop_or_period';
      END IF;
      windows:=array_append(windows,query->>'window');
    ELSIF source.domain='finance' THEN
      IF source.temporal_role IS DISTINCT FROM 'monthly_context'
         OR source.source_revision IS DISTINCT FROM current_finance
         OR query->'analysisPeriod' IS NULL
      THEN RAISE EXCEPTION 'ai_v4_report_link_finance_invalid'; END IF;
    ELSE RAISE EXCEPTION 'ai_v4_report_link_domain_invalid'; END IF;
    source_rows:=source_rows||jsonb_build_array(jsonb_build_object(
      'sourceKey',source.source_key,'ordinal',source.ordinal,
      'domain',source.domain,'window',query->>'window',
      'queryDigest',source.query_digest,
      'sourceIdentityDigest',source.source_identity_digest,
      'sourceRef',source.source_ref,'sourceRevision',source.source_revision,
      'sourceVersion',source.version,'pageCount',source.page_count,
      'rowCount',source.row_count,'storedBytes',source.stored_bytes));
  END LOOP;
  IF count_rows IS DISTINCT FROM 4 OR (SELECT count(*) FROM unnest(windows) AS w) IS DISTINCT FROM 3
     OR (SELECT array_agg(w ORDER BY w) FROM unnest(windows) AS w)
         IS DISTINCT FROM ARRAY['current','previous','yearAgo']::text[]
     OR origin_end::date-origin_start::date NOT BETWEEN 0 AND 92
     OR (SELECT count(*) FROM public.ai_business_v4_sources
         WHERE run_id=parent.id AND domain='finance') IS DISTINCT FROM 1
     OR NOT EXISTS (SELECT 1 FROM public.ai_business_v4_sources f
         WHERE f.run_id=parent.id AND f.domain='finance'
           AND f.query_json::jsonb->'analysisPeriod'=
             jsonb_build_object('startDate',origin_start,'endDate',origin_end))
  THEN RAISE EXCEPTION 'ai_v4_report_link_three_windows_invalid'; END IF;
  RETURN jsonb_build_object('schemaVersion','business-v4-report-source-bindings-v1',
    'shop',shop,'originalStartDate',origin_start,'originalEndDate',origin_end,
    'sources',source_rows,'financeShopMappingVerified',false,
    'authorityVerified',false);
END $$"""


ISSUE_SQL = """CREATE FUNCTION public.ai_v4_issue_report_link_intent(
  selected_report text,selected_v4_run text,actor_email text,
  selected_v2_run text,expected_v2_seal text,expected_v4_seal text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE v2 public.ai_business_evidence_runs%ROWTYPE;
  v4 public.ai_business_v4_runs%ROWTYPE;
  seal public.ai_business_v4_seals%ROWTYPE;
  actor_version bigint; bound jsonb;
BEGIN
  IF session_user IS DISTINCT FROM 'teruisi_ai_writer'
     OR selected_report !~ '^[A-Za-z0-9_-]{1,160}$'
     OR actor_email IS DISTINCT FROM lower(btrim(actor_email))
     OR expected_v2_seal !~ '^[0-9a-f]{64}$'
     OR expected_v4_seal !~ '^[0-9a-f]{64}$'
     OR EXISTS (SELECT 1 FROM public.ai_report_runs WHERE id=selected_report)
  THEN RAISE EXCEPTION 'ai_v4_report_link_issue_denied'; END IF;
  SELECT u.version INTO actor_version FROM public.access_control_users u
    WHERE u.email=actor_email AND u.role='admin' AND u.status='active'
      AND u.scope IS NULL FOR SHARE;
  SELECT * INTO v2 FROM public.ai_business_evidence_runs WHERE id=selected_v2_run;
  SELECT * INTO v4 FROM public.ai_business_v4_runs WHERE id=selected_v4_run;
  SELECT * INTO seal FROM public.ai_business_v4_seals WHERE run_id=selected_v4_run;
  IF actor_version IS NULL OR v2.id IS NULL OR v4.id IS NULL OR seal.run_id IS NULL
     OR v2.owner_email IS DISTINCT FROM actor_email OR v4.owner_email IS DISTINCT FROM actor_email
     OR v2.scope_json IS DISTINCT FROM 'null' OR v4.scope_json IS DISTINCT FROM 'null'
     OR v2.status IS DISTINCT FROM 'sealed' OR v4.status IS DISTINCT FROM 'sealed'
     OR v2.state_json::jsonb->>'sealedDigest' IS DISTINCT FROM expected_v2_seal
     OR seal.body_digest IS DISTINCT FROM expected_v4_seal
     OR seal.evidence_version IS DISTINCT FROM v4.version
  THEN RAISE EXCEPTION 'ai_v4_report_link_issue_source_invalid'; END IF;
  bound:=public.ai_v4_report_source_bindings(v4.id);
  INSERT INTO public.protected_business_v4_report_link_intents
    (report_id,v4_run_id,owner_email,actor_version,v2_run_id,
     v2_sealed_digest,v4_sealed_digest,source_bindings_digest,
     issued_txid,issued_at)
  VALUES(selected_report,v4.id,actor_email,actor_version,v2.id,
     expected_v2_seal,expected_v4_seal,
     encode(sha256(convert_to(bound::text,'UTF8')),'hex'),
     txid_current(),clock_timestamp());
  RETURN selected_report;
END $$"""


CREATE_REPORT_SQL = """CREATE FUNCTION public.ai_v4_create_report_from_link_intent(
  selected_report text,actor_email text,selected_client_request text,
  selected_request_digest text,selected_workflow text,snapshot_text text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE intent public.protected_business_v4_report_link_intents%ROWTYPE;
  flow public.ai_workflow_runs%ROWTYPE;
  authority public.ai_write_authority%ROWTYPE;
  snapshot jsonb;
BEGIN
  IF session_user IS DISTINCT FROM 'teruisi_ai_writer'
     OR selected_report !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selected_client_request !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selected_request_digest !~ '^[0-9a-f]{64}$'
     OR snapshot_text IS NULL OR octet_length(snapshot_text)>32768
     OR actor_email IS DISTINCT FROM lower(btrim(actor_email))
     OR EXISTS (SELECT 1 FROM public.ai_report_runs WHERE id=selected_report)
  THEN RAISE EXCEPTION 'ai_v4_report_link_create_denied'; END IF;
  -- Hold the lifecycle row through intent/flow waits and the report INSERT.
  SELECT * INTO authority FROM public.ai_write_authority
    WHERE id=1 FOR SHARE;
  IF authority.id IS NULL OR authority.status IS DISTINCT FROM 'postgres'
     OR authority.authority_epoch::text IS DISTINCT FROM
          current_setting('teruisi.ai_epoch',true)
     OR authority.cutover_id IS DISTINCT FROM
          current_setting('teruisi.ai_cutover',true)
  THEN RAISE EXCEPTION 'ai_v4_report_link_create_authority_invalid'; END IF;
  SELECT * INTO intent FROM public.protected_business_v4_report_link_intents
    WHERE report_id=selected_report FOR UPDATE;
  SELECT * INTO flow FROM public.ai_workflow_runs
    WHERE id=selected_workflow FOR SHARE;
  snapshot:=snapshot_text::jsonb;
  IF intent.report_id IS NULL OR intent.issued_txid IS DISTINCT FROM txid_current()
     OR intent.owner_email IS DISTINCT FROM actor_email
     OR flow.id IS NULL OR flow.owner_email IS DISTINCT FROM actor_email
     OR flow.scope_json IS DISTINCT FROM 'null'
     OR snapshot->>'schemaVersion' IS DISTINCT FROM 'business-report-v1'
     OR snapshot->>'executionProfile' IS DISTINCT FROM
          'business-agent-integrated-reference-v1'
     OR snapshot->>'evidenceProtocol' IS DISTINCT FROM 'reference-v2'
     OR snapshot->>'reportId' IS DISTINCT FROM selected_report
     OR snapshot->>'evidenceRunId' IS DISTINCT FROM intent.v2_run_id
     OR snapshot->>'sealedDigest' IS DISTINCT FROM intent.v2_sealed_digest
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users u
         WHERE u.email=actor_email AND u.role='admin'
           AND u.status='active' AND u.scope IS NULL
           AND u.version=intent.actor_version)
  THEN RAISE EXCEPTION 'ai_v4_report_link_create_identity_invalid'; END IF;
  -- Repeat the current-state check immediately before the definer INSERT.
  SELECT * INTO authority FROM public.ai_write_authority
    WHERE id=1 FOR SHARE;
  IF authority.id IS NULL OR authority.status IS DISTINCT FROM 'postgres'
     OR authority.authority_epoch::text IS DISTINCT FROM
          current_setting('teruisi.ai_epoch',true)
     OR authority.cutover_id IS DISTINCT FROM
          current_setting('teruisi.ai_cutover',true)
  THEN RAISE EXCEPTION 'ai_v4_report_link_create_authority_invalid'; END IF;
  INSERT INTO public.ai_report_runs
    (id,owner_email,scope_json,client_request_id,request_digest,
     workflow_id,budget_plan_id,snapshot_json,created_at)
  VALUES(selected_report,actor_email,'null',selected_client_request,
     selected_request_digest,flow.id,NULL,snapshot_text,clock_timestamp());
  -- Existing report guards and 0071 AFTER INSERT trigger must all succeed.
  RETURN selected_report;
END $$"""


REPORT_TRIGGER = """CREATE FUNCTION public.ai_v4_report_link_after_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE intent public.protected_business_v4_report_link_intents%ROWTYPE;
  v2 public.ai_business_evidence_runs%ROWTYPE;
  v4 public.ai_business_v4_runs%ROWTYPE;
  seal public.ai_business_v4_seals%ROWTYPE;
  flow public.ai_workflow_runs%ROWTYPE;
  snapshot jsonb; header jsonb; plan jsonb; bound jsonb;
BEGIN
  SELECT * INTO intent FROM public.protected_business_v4_report_link_intents
    WHERE report_id=NEW.id FOR UPDATE;
  IF intent.report_id IS NULL THEN RETURN NEW; END IF;
  SELECT * INTO v2 FROM public.ai_business_evidence_runs WHERE id=intent.v2_run_id;
  SELECT * INTO v4 FROM public.ai_business_v4_runs WHERE id=intent.v4_run_id;
  SELECT * INTO seal FROM public.ai_business_v4_seals WHERE run_id=intent.v4_run_id;
  SELECT * INTO flow FROM public.ai_workflow_runs WHERE id=NEW.workflow_id;
  snapshot:=NEW.snapshot_json::jsonb;
  header:=v2.plan_json::jsonb; plan:=v4.plan_json::jsonb;
  IF session_user IS DISTINCT FROM 'teruisi_ai_writer'
     OR intent.issued_txid IS DISTINCT FROM txid_current()
     OR intent.owner_email IS DISTINCT FROM NEW.owner_email OR NEW.scope_json IS DISTINCT FROM 'null'
     OR NEW.owner_email IS DISTINCT FROM v2.owner_email OR NEW.owner_email IS DISTINCT FROM v4.owner_email
     OR flow.id IS NULL OR flow.owner_email IS DISTINCT FROM NEW.owner_email
     OR flow.scope_json IS DISTINCT FROM NEW.scope_json
     OR v2.id IS NULL OR v4.id IS NULL OR seal.run_id IS NULL
     OR snapshot->>'executionProfile' IS DISTINCT FROM 'business-agent-integrated-reference-v1'
     OR snapshot->>'evidenceProtocol' IS DISTINCT FROM 'reference-v2'
     OR snapshot->>'reportId' IS DISTINCT FROM NEW.id
     OR snapshot->>'evidenceRunId' IS DISTINCT FROM v2.id
     OR snapshot->>'evidenceVersion' IS DISTINCT FROM v2.version::text
     OR snapshot->>'sealedDigest' IS DISTINCT FROM intent.v2_sealed_digest
     OR v2.state_json::jsonb->>'sealedDigest' IS DISTINCT FROM intent.v2_sealed_digest
     OR snapshot->'scope' IS DISTINCT FROM
       jsonb_build_object('platform','京东',
         'shop',(public.ai_v4_report_source_bindings(v4.id))->>'shop',
         'startDate',(public.ai_v4_report_source_bindings(v4.id))->>'originalStartDate',
         'endDate',(public.ai_v4_report_source_bindings(v4.id))->>'originalEndDate')
     OR header->'analysisRequest' IS DISTINCT FROM plan->'analysisRequest'
     OR snapshot->>'question' IS DISTINCT FROM plan->'analysisRequest'->>'question'
     OR seal.body_digest IS DISTINCT FROM intent.v4_sealed_digest
     OR seal.evidence_version IS DISTINCT FROM v4.version
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users a
         WHERE a.email=intent.owner_email AND a.role='admin'
           AND a.status='active' AND a.scope IS NULL
           AND a.version=intent.actor_version)
  THEN RAISE EXCEPTION 'ai_v4_report_link_report_identity_invalid'; END IF;
  bound:=public.ai_v4_report_source_bindings(v4.id);
  IF encode(sha256(convert_to(bound::text,'UTF8')),'hex')
        IS DISTINCT FROM intent.source_bindings_digest
  THEN RAISE EXCEPTION 'ai_v4_report_link_issue_revision_drift'; END IF;
  INSERT INTO public.protected_business_v4_report_source_links
    (report_id,v4_run_id,owner_email,actor_version,v2_run_id,
     v2_evidence_version,v2_sealed_digest,v4_evidence_version,
     v4_plan_digest,v4_sealed_digest,v4_mac_digest,
     report_snapshot_digest,workflow_input_digest,
     source_bindings_json,source_bindings_digest,
     created_txid,created_at,authority_verified,report_generation_supported)
  VALUES(NEW.id,v4.id,NEW.owner_email,intent.actor_version,v2.id,
     v2.version,intent.v2_sealed_digest,v4.version,v4.plan_digest,
     seal.body_digest,
     encode(sha256(convert_to(seal.body_mac,'UTF8')),'hex'),
     encode(sha256(convert_to(NEW.snapshot_json,'UTF8')),'hex'),
     encode(sha256(convert_to(flow.input_json,'UTF8')),'hex'),
     bound::text,intent.source_bindings_digest,
     txid_current(),clock_timestamp(),false,false);
  RETURN NEW;
END $$"""


READ_SQL = """CREATE FUNCTION public.ai_v4_read_report_source_link(
  selected_report text,actor_email text,expected_actor_version bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE link public.protected_business_v4_report_source_links%ROWTYPE;
  report public.ai_report_runs%ROWTYPE;
  flow public.ai_workflow_runs%ROWTYPE;
  v2 public.ai_business_evidence_runs%ROWTYPE;
  v4 public.ai_business_v4_runs%ROWTYPE;
  seal public.ai_business_v4_seals%ROWTYPE;
  bound jsonb;
BEGIN
  IF session_user IS DISTINCT FROM 'teruisi_ai_reader' THEN
    RAISE EXCEPTION 'ai_v4_report_link_reader_identity_denied'; END IF;
  SELECT * INTO link FROM public.protected_business_v4_report_source_links WHERE report_id=selected_report;
  IF link.report_id IS NULL THEN RAISE EXCEPTION 'ai_v4_report_link_absent'; END IF;
  SELECT * INTO report FROM public.ai_report_runs WHERE id=link.report_id;
  SELECT * INTO flow FROM public.ai_workflow_runs WHERE id=report.workflow_id;
  SELECT * INTO v2 FROM public.ai_business_evidence_runs WHERE id=link.v2_run_id;
  SELECT * INTO v4 FROM public.ai_business_v4_runs WHERE id=link.v4_run_id;
  SELECT * INTO seal FROM public.ai_business_v4_seals WHERE run_id=link.v4_run_id;
  IF actor_email IS DISTINCT FROM link.owner_email OR expected_actor_version IS DISTINCT FROM link.actor_version
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users a
         WHERE a.email=actor_email AND a.role='admin' AND a.status='active'
           AND a.scope IS NULL AND a.version=expected_actor_version)
     OR report.id IS NULL OR flow.id IS NULL OR v2.id IS NULL OR v4.id IS NULL
     OR seal.run_id IS NULL OR report.owner_email IS DISTINCT FROM link.owner_email
     OR flow.owner_email IS DISTINCT FROM link.owner_email OR v2.owner_email IS DISTINCT FROM link.owner_email
     OR v4.owner_email IS DISTINCT FROM link.owner_email
     OR report.scope_json IS DISTINCT FROM 'null' OR v2.scope_json IS DISTINCT FROM 'null' OR v4.scope_json IS DISTINCT FROM 'null'
     OR v2.status IS DISTINCT FROM 'sealed' OR v4.status IS DISTINCT FROM 'sealed'
     OR v2.version IS DISTINCT FROM link.v2_evidence_version
     OR v4.version IS DISTINCT FROM link.v4_evidence_version
     OR v2.state_json::jsonb->>'sealedDigest' IS DISTINCT FROM link.v2_sealed_digest
     OR seal.body_digest IS DISTINCT FROM link.v4_sealed_digest
     OR seal.evidence_version IS DISTINCT FROM link.v4_evidence_version
     OR v4.plan_digest IS DISTINCT FROM link.v4_plan_digest
     OR encode(sha256(convert_to(seal.body_mac,'UTF8')),'hex') IS DISTINCT FROM link.v4_mac_digest
     OR encode(sha256(convert_to(report.snapshot_json,'UTF8')),'hex') IS DISTINCT FROM
          link.report_snapshot_digest
     OR encode(sha256(convert_to(flow.input_json,'UTF8')),'hex') IS DISTINCT FROM
          link.workflow_input_digest
     OR link.authority_verified OR link.report_generation_supported
  THEN RAISE EXCEPTION 'ai_v4_report_link_current_identity_invalid'; END IF;
  bound:=public.ai_v4_report_source_bindings(v4.id);
  IF bound::text IS DISTINCT FROM link.source_bindings_json
     OR encode(sha256(convert_to(bound::text,'UTF8')),'hex') IS DISTINCT FROM
          link.source_bindings_digest
  THEN RAISE EXCEPTION 'ai_v4_report_link_current_source_drift'; END IF;
  RETURN jsonb_build_object('schemaVersion','business-v4-report-link-read-candidate-v1',
    'reportId',link.report_id,'v2EvidenceRunId',link.v2_run_id,
    'v2EvidenceVersion',link.v2_evidence_version,
    'v2SealedDigest',link.v2_sealed_digest,'v4RunId',link.v4_run_id,
    'v4EvidenceVersion',link.v4_evidence_version,
    'v4PlanDigest',link.v4_plan_digest,'v4SealedDigest',link.v4_sealed_digest,
    'reportSnapshotDigest',link.report_snapshot_digest,
    'workflowInputDigest',link.workflow_input_digest,
    'sourceBindingsDigest',link.source_bindings_digest,
    'sourceBindings',bound,
    'creationTimeLinkPersisted',true,'appHmacVerified',false,
    'authorityVerified',false,'reportGenerationSupported',false,
    'agentDispatchSupported',false,'rendererRegistered',false,
    'downloadSupported',false);
END $$"""

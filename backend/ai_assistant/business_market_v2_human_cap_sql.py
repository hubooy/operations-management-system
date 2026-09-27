"""SQL-owned explicit CNY ceiling; it never creates spendable model authority."""

APPROVAL = "public.protected_business_market_v2_human_cap_approvals"
REVOCATION = "public.protected_business_market_v2_human_cap_revocations"
WRITER = "teruisi_ai_writer"
GUARD_SIG = "public.ai_market_v2_human_cap_guard()"
MODEL_SIG = "public.ai_market_v2_human_cap_model_digest(text)"
PREVIEW_SIG = "public.ai_market_v2_human_cap_preview(text,text,bigint)"
APPROVE_SIG = "public.ai_market_v2_approve_human_cap(text,text,text,bigint)"
REVOKE_SIG = "public.ai_market_v2_revoke_human_cap(text,text,bigint,text)"
OUTCOME_SIG = "public.ai_market_v2_human_cap_outcome(text,text,bigint)"

GUARD = """CREATE FUNCTION public.ai_market_v2_human_cap_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'ai_market_human_cap_immutable'; END IF;
  IF session_user<>'teruisi_ai_writer'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT relowner FROM pg_catalog.pg_class WHERE oid=TG_RELID))
  THEN RAISE EXCEPTION 'ai_market_human_cap_direct_write_denied'; END IF;
  RETURN NEW;
END $$"""

# No credential or suffix enters this hash. The raw base URL and generation
# options are included, so changing either without a version bump invalidates
# an approval. It is a local config identity, not provider/account authority.
MODEL = """CREATE FUNCTION public.ai_market_v2_human_cap_model_digest(
  selected_model text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE model record; body jsonb;
BEGIN
  SELECT id,version,status,model_type,protocol,model_name,base_url,
    generation_options_json,max_tokens,max_tool_rounds,max_total_tool_calls,
    timeout_ms,reasoning_mode,temperature_milli INTO model
    FROM public.ai_models WHERE id=selected_model FOR SHARE;
  IF model.id IS NULL OR model.status<>'enabled' OR model.model_type<>'text'
  THEN RAISE EXCEPTION 'ai_market_human_cap_model_unavailable'; END IF;
  body:=jsonb_build_object('id',model.id,'version',model.version,
    'protocol',model.protocol,'modelName',model.model_name,
    'baseUrl',model.base_url,'generationOptionsJson',model.generation_options_json,
    'maxTokens',model.max_tokens,'maxToolRounds',model.max_tool_rounds,
    'maxTotalToolCalls',model.max_total_tool_calls,'timeoutMs',model.timeout_ms,
    'reasoningMode',model.reasoning_mode,
    'temperatureMilli',model.temperature_milli);
  RETURN encode(sha256(convert_to(public.ai_v4_replay_canonical(body),
    'UTF8')),'hex');
END $$"""

PREVIEW = """CREATE FUNCTION public.ai_market_v2_human_cap_preview(
  selected_ledger text, selected_actor text, selected_actor_version bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE ledger public.ai_business_market_v2_cost_ledger_candidates%ROWTYPE;
  plan public.ai_business_market_v2_execution_plans%ROWTYPE;
  report public.ai_report_runs%ROWTYPE;
  actor public.access_control_users%ROWTYPE;
  model record;
BEGIN
  IF session_user<>'teruisi_ai_writer' OR selected_ledger IS NULL
     OR selected_ledger !~ '^[0-9a-f]{64}$'
     OR selected_actor IS NULL OR selected_actor<>lower(selected_actor)
     OR selected_actor_version IS NULL OR selected_actor_version<1
  THEN RAISE EXCEPTION 'ai_market_human_cap_preview_unavailable'; END IF;
  SELECT * INTO ledger FROM public.ai_business_market_v2_cost_ledger_candidates
    WHERE id=selected_ledger FOR SHARE;
  SELECT * INTO plan FROM public.ai_business_market_v2_execution_plans
    WHERE id=ledger.plan_id FOR SHARE;
  SELECT * INTO report FROM public.ai_report_runs
    WHERE id=plan.execution_report_id FOR SHARE;
  SELECT * INTO actor FROM public.access_control_users
    WHERE email=selected_actor FOR SHARE;
  SELECT id,version INTO model FROM public.ai_models
    WHERE id=ledger.model_id FOR SHARE;
  IF ledger.id IS NULL OR plan.id IS NULL OR report.id IS NULL
     OR actor.email IS NULL OR actor.role<>'admin' OR actor.status<>'active'
     OR actor.scope IS NOT NULL OR actor.version IS DISTINCT FROM selected_actor_version
     OR ledger.owner_email IS DISTINCT FROM actor.email
     OR report.owner_email IS DISTINCT FROM actor.email
     OR report.scope_json IS DISTINCT FROM 'null'
     OR model.id IS NULL OR model.version IS DISTINCT FROM ledger.model_version
     OR ledger.status<>'pending_rate_and_approval_verification'
     OR ledger.reserved_cents<>0
     OR ledger.candidate_digest IS DISTINCT FROM encode(sha256(convert_to(
       ledger.candidate_json,'UTF8')),'hex')
     OR plan.plan_digest IS DISTINCT FROM encode(sha256(convert_to(
       plan.plan_json,'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_market_human_cap_preview_binding_changed'; END IF;
  PERFORM public.ai_market_v2_execution_plan_expected(
    report.id,plan.plan_json);
  RETURN jsonb_build_object('schemaVersion',
    'business-market-v2-human-cap-preview-v1',
    'ledgerId',ledger.id,'planId',plan.id,'reportId',report.id,
    'ledgerDigest',ledger.candidate_digest,'planDigest',plan.plan_digest,
    'reportSnapshotDigest',encode(sha256(convert_to(
      report.snapshot_json,'UTF8')),'hex'),
    'modelConfigDigest',public.ai_market_v2_human_cap_model_digest(model.id),
    'modelId',model.id,'modelVersion',model.version,
    'requiredClaimCents',ledger.required_cents,
    'maximumClaimCents',ledger.cap_claim_cents,'currency','CNY',
    'tariffAuthorityVerified',false,'credentialAccountVerified',false,
    'fundsReserved',false,'reservedCents',0,'providerCallsAllowed',false);
END $$"""

APPROVE = """CREATE FUNCTION public.ai_market_v2_approve_human_cap(
  selected_ledger text, request_text text, selected_actor text,
  selected_actor_version bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE value jsonb; ledger public.ai_business_market_v2_cost_ledger_candidates%ROWTYPE;
  plan public.ai_business_market_v2_execution_plans%ROWTYPE;
  report public.ai_report_runs%ROWTYPE;
  actor public.access_control_users%ROWTYPE;
  model record;
  saved public.protected_business_market_v2_human_cap_approvals%ROWTYPE;
  approval_row_id text; request_digest text; model_digest text;
  expiry timestamptz; cap bigint;
BEGIN
  IF session_user<>'teruisi_ai_writer'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT relowner FROM pg_catalog.pg_class WHERE
         oid='public.protected_business_market_v2_human_cap_approvals'::regclass))
     OR selected_ledger IS NULL OR selected_ledger !~ '^[0-9a-f]{64}$'
     OR selected_actor IS NULL OR selected_actor<>lower(selected_actor)
     OR selected_actor_version IS NULL OR selected_actor_version<1
     OR request_text IS NULL OR octet_length(request_text) NOT BETWEEN 1 AND 4096
  THEN RAISE EXCEPTION 'ai_market_human_cap_writer_unavailable'; END IF;
  value:=request_text::jsonb;
  IF jsonb_typeof(value) IS DISTINCT FROM 'object'
     OR request_text IS DISTINCT FROM public.ai_v4_replay_canonical(value)
     OR NOT value ?& ARRAY['schemaVersion','ledgerId','planId','reportId',
       'ledgerDigest','planDigest','reportSnapshotDigest','modelConfigDigest',
       'approvedCapCents','expiresAtUtc','explicitApproval']
     OR value-ARRAY['schemaVersion','ledgerId','planId','reportId',
       'ledgerDigest','planDigest','reportSnapshotDigest','modelConfigDigest',
       'approvedCapCents','expiresAtUtc','explicitApproval'] <> '{}'::jsonb
     OR value->>'schemaVersion' IS DISTINCT FROM
       'business-market-v2-human-cap-approval-request-v1'
     OR value->'explicitApproval' IS DISTINCT FROM 'true'::jsonb
     OR jsonb_typeof(value->'approvedCapCents') IS DISTINCT FROM 'number'
     OR value->>'approvedCapCents' !~ '^[0-9]{1,9}$'
     OR value->>'expiresAtUtc' !~
       '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
  THEN RAISE EXCEPTION 'ai_market_human_cap_request_invalid'; END IF;
  cap:=(value->>'approvedCapCents')::bigint;
  expiry:=(value->>'expiresAtUtc')::timestamptz;
  IF expiry<=clock_timestamp() OR expiry>clock_timestamp()+interval '31 days'
  THEN RAISE EXCEPTION 'ai_market_human_cap_expiry_invalid'; END IF;
  -- Existing 0065/0063 records are immutable. Lock in the same order as
  -- cost admission, then validate the current report and administrator.
  SELECT * INTO ledger FROM public.ai_business_market_v2_cost_ledger_candidates
    WHERE id=selected_ledger FOR SHARE;
  IF ledger.id IS NULL THEN RAISE EXCEPTION 'ai_market_human_cap_ledger_missing'; END IF;
  SELECT * INTO plan FROM public.ai_business_market_v2_execution_plans
    WHERE id=ledger.plan_id FOR SHARE;
  SELECT * INTO report FROM public.ai_report_runs
    WHERE id=plan.execution_report_id FOR SHARE;
  SELECT * INTO actor FROM public.access_control_users
    WHERE email=selected_actor FOR SHARE;
  SELECT id,version INTO model FROM public.ai_models
    WHERE id=ledger.model_id FOR SHARE;
  model_digest:=public.ai_market_v2_human_cap_model_digest(ledger.model_id);
  IF plan.id IS NULL OR report.id IS NULL OR actor.email IS NULL
     OR actor.role<>'admin' OR actor.status<>'active' OR actor.scope IS NOT NULL
     OR actor.version IS DISTINCT FROM selected_actor_version
     OR ledger.owner_email IS DISTINCT FROM selected_actor
     OR report.owner_email IS DISTINCT FROM selected_actor
     OR report.scope_json IS DISTINCT FROM 'null'
     OR report.id IS DISTINCT FROM plan.execution_report_id
     OR model.id IS NULL OR model.version IS DISTINCT FROM ledger.model_version
     OR ledger.status<>'pending_rate_and_approval_verification'
     OR ledger.reserved_cents<>0
     OR ledger.required_cents NOT BETWEEN 1 AND cap
     OR cap NOT BETWEEN 1 AND ledger.cap_claim_cents
     OR ledger.candidate_digest IS DISTINCT FROM encode(sha256(convert_to(
       ledger.candidate_json,'UTF8')),'hex')
     OR plan.plan_digest IS DISTINCT FROM encode(sha256(convert_to(
       plan.plan_json,'UTF8')),'hex')
     OR value->>'ledgerId' IS DISTINCT FROM ledger.id
     OR value->>'planId' IS DISTINCT FROM plan.id
     OR value->>'reportId' IS DISTINCT FROM report.id
     OR value->>'ledgerDigest' IS DISTINCT FROM ledger.candidate_digest
     OR value->>'planDigest' IS DISTINCT FROM plan.plan_digest
     OR value->>'reportSnapshotDigest' IS DISTINCT FROM encode(sha256(
       convert_to(report.snapshot_json,'UTF8')),'hex')
     OR value->>'modelConfigDigest' IS DISTINCT FROM model_digest
  THEN RAISE EXCEPTION 'ai_market_human_cap_binding_changed'; END IF;
  PERFORM public.ai_market_v2_execution_plan_expected(
    report.id,plan.plan_json);
  approval_row_id:=encode(sha256(convert_to(
    'market-human-cap-v1|'||ledger.id,'UTF8')),'hex');
  request_digest:=encode(sha256(convert_to(request_text,'UTF8')),'hex');
  IF EXISTS(SELECT 1 FROM public.protected_business_market_v2_human_cap_revocations rev
      WHERE rev.approval_id=approval_row_id)
  THEN RAISE EXCEPTION 'ai_market_human_cap_revoked'; END IF;
  INSERT INTO public.protected_business_market_v2_human_cap_approvals(
    id,ledger_id,plan_id,report_id,owner_email,owner_version,model_id,
    model_version,model_config_digest,ledger_digest,plan_digest,
    report_snapshot_digest,approved_cap_cents,expires_at,request_json,
    request_digest,created_at)
  VALUES(approval_row_id,ledger.id,plan.id,report.id,selected_actor,
    selected_actor_version,model.id,model.version,model_digest,
    ledger.candidate_digest,plan.plan_digest,
    value->>'reportSnapshotDigest',cap,expiry,request_text,request_digest,
    clock_timestamp()) ON CONFLICT(id) DO NOTHING;
  SELECT * INTO saved FROM public.protected_business_market_v2_human_cap_approvals
    WHERE id=approval_row_id;
  IF saved.id IS NULL OR saved.ledger_id IS DISTINCT FROM ledger.id
     OR saved.request_digest IS DISTINCT FROM request_digest
     OR saved.request_json IS DISTINCT FROM request_text
     OR saved.owner_email IS DISTINCT FROM selected_actor
     OR saved.owner_version IS DISTINCT FROM selected_actor_version
     OR saved.expires_at<=clock_timestamp()
  THEN RAISE EXCEPTION 'ai_market_human_cap_conflicting_replay'; END IF;
  RETURN jsonb_build_object('schemaVersion',
    'business-market-v2-human-cap-receipt-v1',
    'approvalId',saved.id,'ledgerId',ledger.id,'planId',plan.id,
    'reportId',report.id,'approvedCapCents',saved.approved_cap_cents,
    'expiresAtUtc',to_char(saved.expires_at AT TIME ZONE 'UTC',
      'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    'requestDigest',saved.request_digest,'status','approved_cap_only',
    'tariffAuthorityVerified',false,'credentialAccountVerified',false,
    'fundsReserved',false,'reservedCents',0,'providerCallsAllowed',false);
END $$"""

REVOKE = """CREATE FUNCTION public.ai_market_v2_revoke_human_cap(
  selected_approval text, selected_actor text, selected_actor_version bigint,
  selected_reason_digest text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE saved public.protected_business_market_v2_human_cap_approvals%ROWTYPE;
  actor public.access_control_users%ROWTYPE; revoke_id text;
BEGIN
  IF session_user<>'teruisi_ai_writer'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT relowner FROM pg_catalog.pg_class WHERE
         oid='public.protected_business_market_v2_human_cap_revocations'::regclass))
     OR selected_approval IS NULL OR selected_approval !~ '^[0-9a-f]{64}$'
     OR selected_reason_digest IS NULL
     OR selected_reason_digest !~ '^[0-9a-f]{64}$'
     OR selected_actor IS NULL OR selected_actor<>lower(selected_actor)
  THEN RAISE EXCEPTION 'ai_market_human_cap_revoke_unavailable'; END IF;
  SELECT * INTO saved FROM public.protected_business_market_v2_human_cap_approvals
    WHERE id=selected_approval FOR SHARE;
  SELECT * INTO actor FROM public.access_control_users
    WHERE email=selected_actor FOR SHARE;
  IF saved.id IS NULL OR actor.email IS NULL OR actor.role<>'admin'
     OR actor.status<>'active' OR actor.scope IS NOT NULL
     OR actor.version IS DISTINCT FROM selected_actor_version
     OR saved.owner_email IS DISTINCT FROM actor.email
  THEN RAISE EXCEPTION 'ai_market_human_cap_revoke_actor_invalid'; END IF;
  revoke_id:=encode(sha256(convert_to(
    'market-human-cap-revoke-v1|'||saved.id,'UTF8')),'hex');
  INSERT INTO public.protected_business_market_v2_human_cap_revocations(
    id,approval_id,actor_email,actor_version,reason_digest,created_at)
  VALUES(revoke_id,saved.id,actor.email,actor.version,
    selected_reason_digest,clock_timestamp()) ON CONFLICT(id) DO NOTHING;
  IF NOT EXISTS(SELECT 1 FROM public.protected_business_market_v2_human_cap_revocations rev
      WHERE rev.id=revoke_id AND rev.approval_id=saved.id
      AND rev.reason_digest=selected_reason_digest
      AND rev.actor_email=actor.email AND rev.actor_version=actor.version)
  THEN RAISE EXCEPTION 'ai_market_human_cap_revoke_conflict'; END IF;
  RETURN jsonb_build_object('approvalId',saved.id,'status','revoked',
    'fundsReserved',false,'providerCallsAllowed',false);
END $$"""

OUTCOME = """CREATE FUNCTION public.ai_market_v2_human_cap_outcome(
  selected_ledger text, selected_actor text, selected_actor_version bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor public.access_control_users%ROWTYPE;
  saved public.protected_business_market_v2_human_cap_approvals%ROWTYPE;
  ledger public.ai_business_market_v2_cost_ledger_candidates%ROWTYPE;
  plan public.ai_business_market_v2_execution_plans%ROWTYPE;
  report public.ai_report_runs%ROWTYPE;
  model record;
  status text; current_model_digest text;
BEGIN
  IF session_user<>'teruisi_ai_writer' OR selected_ledger IS NULL
     OR selected_ledger !~ '^[0-9a-f]{64}$'
     OR selected_actor IS NULL OR selected_actor<>lower(selected_actor)
  THEN RAISE EXCEPTION 'ai_market_human_cap_outcome_unavailable'; END IF;
  SELECT actor_user.* INTO actor FROM public.access_control_users actor_user
    WHERE actor_user.email=selected_actor;
  SELECT cost.* INTO ledger FROM public.ai_business_market_v2_cost_ledger_candidates cost
    WHERE cost.id=selected_ledger;
  IF actor.email IS NULL OR actor.role<>'admin' OR actor.status<>'active'
     OR actor.scope IS NOT NULL OR actor.version IS DISTINCT FROM selected_actor_version
     OR ledger.id IS NULL OR ledger.owner_email IS DISTINCT FROM actor.email
  THEN RAISE EXCEPTION 'ai_market_human_cap_outcome_actor_invalid'; END IF;
  SELECT approval.* INTO saved FROM public.protected_business_market_v2_human_cap_approvals approval
    WHERE approval.ledger_id=ledger.id;
  SELECT plan_item.* INTO plan FROM public.ai_business_market_v2_execution_plans plan_item
    WHERE plan_item.id=ledger.plan_id;
  SELECT report_item.* INTO report FROM public.ai_report_runs report_item
    WHERE report_item.id=plan.execution_report_id;
  SELECT current_model.id,current_model.version,current_model.status,
    current_model.model_type INTO model FROM public.ai_models current_model
    WHERE current_model.id=saved.model_id FOR SHARE;
  IF saved.id IS NULL THEN status:='absent';
  ELSIF EXISTS(SELECT 1 FROM public.protected_business_market_v2_human_cap_revocations rev
      WHERE rev.approval_id=saved.id) THEN status:='revoked';
  ELSIF saved.expires_at<=clock_timestamp() THEN status:='expired';
  ELSIF plan.id IS NULL OR report.id IS NULL
     OR saved.owner_version IS DISTINCT FROM actor.version
     OR saved.ledger_digest IS DISTINCT FROM ledger.candidate_digest
     OR saved.plan_digest IS DISTINCT FROM plan.plan_digest
     OR saved.report_snapshot_digest IS DISTINCT FROM encode(sha256(
       convert_to(report.snapshot_json,'UTF8')),'hex')
     OR saved.model_version IS DISTINCT FROM ledger.model_version
     OR model.id IS NULL OR model.version IS DISTINCT FROM saved.model_version
     OR model.status IS DISTINCT FROM 'enabled'
     OR model.model_type IS DISTINCT FROM 'text'
  THEN status:='stale';
  ELSE
    current_model_digest:=public.ai_market_v2_human_cap_model_digest(
      saved.model_id);
    status:=CASE WHEN current_model_digest=saved.model_config_digest
      THEN 'approved_cap_only' ELSE 'stale' END;
  END IF;
  RETURN jsonb_build_object('schemaVersion',
    'business-market-v2-human-cap-outcome-v1',
    'approvalId',saved.id,'ledgerId',ledger.id,'status',status,
    'approvedCapCents',saved.approved_cap_cents,
    'tariffAuthorityVerified',false,'credentialAccountVerified',false,
    'fundsReserved',false,'reservedCents',0,'providerCallsAllowed',false);
END $$"""

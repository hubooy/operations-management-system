"""Frozen 0072 SQL: separate pending rate and human-cap proposals, no grant."""

RATE_ROLE = "teruisi_ai_market_rate_proposer"
CAP_ROLE = "teruisi_ai_market_cap_proposer"
REVOKE_ROLE = "teruisi_ai_market_proposal_revoker"
READER = "teruisi_ai_reader"
WRITER = "teruisi_ai_writer"
RATE = "public.protected_business_market_v2_rate_proposals"
CAP = "public.protected_business_market_v2_cap_proposals"
REVOKE = "public.protected_business_market_v2_authority_revocations"
RATE_SIG = "public.ai_market_v2_record_rate_proposal(text,text)"
CAP_SIG = "public.ai_market_v2_record_cap_proposal(text,text)"
REVOKE_SIG = "public.ai_market_v2_revoke_authority_proposal(text,text,text)"
READ_SIG = "public.ai_market_v2_authority_proposal_receipt(text,text,bigint)"

GUARD = r"""CREATE FUNCTION public.ai_market_v2_authority_proposal_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE wanted text;
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'ai_market_v2_authority_immutable'; END IF;
  wanted:=CASE TG_RELID
    WHEN 'public.protected_business_market_v2_rate_proposals'::regclass
      THEN 'teruisi_ai_market_rate_proposer'
    WHEN 'public.protected_business_market_v2_cap_proposals'::regclass
      THEN 'teruisi_ai_market_cap_proposer'
    WHEN 'public.protected_business_market_v2_authority_revocations'::regclass
      THEN 'teruisi_ai_market_proposal_revoker' ELSE NULL END;
  IF wanted IS NULL OR session_user IS DISTINCT FROM wanted
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT relowner FROM pg_catalog.pg_class WHERE oid=TG_RELID))
  THEN RAISE EXCEPTION 'ai_market_v2_authority_direct_write_denied'; END IF;
  RETURN NEW;
END $$"""

RATE_SQL = r"""CREATE FUNCTION public.ai_market_v2_record_rate_proposal(
  selected_plan text, proposal_text text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE value jsonb; source jsonb; candidate jsonb; root jsonb;
  cost public.ai_business_market_v2_cost_ledger_candidates%ROWTYPE;
  plan public.ai_business_market_v2_execution_plans%ROWTYPE;
  report public.ai_report_runs%ROWTYPE;
  actor public.access_control_users%ROWTYPE;
  model public.ai_models%ROWTYPE;
  saved public.protected_business_market_v2_rate_proposals%ROWTYPE;
  raw_in numeric; raw_out numeric; numerator numeric; denominator numeric;
  row_id text; checksum text;
BEGIN
  IF session_user<>'teruisi_ai_market_rate_proposer'
     OR selected_plan IS NULL OR selected_plan !~ '^[0-9a-f]{64}$'
     OR proposal_text IS NULL OR octet_length(proposal_text) NOT BETWEEN 1 AND 16384
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT relowner FROM pg_catalog.pg_class WHERE
        oid='public.protected_business_market_v2_rate_proposals'::regclass))
     OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles role WHERE
       role.rolname='teruisi_ai_market_rate_proposer' AND NOT role.rolcanlogin
       AND NOT role.rolinherit AND NOT role.rolsuper AND NOT role.rolcreatedb
       AND NOT role.rolcreaterole AND NOT role.rolreplication
       AND NOT role.rolbypassrls)
     OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members member WHERE
       member.roleid='teruisi_ai_market_rate_proposer'::regrole
       OR member.member='teruisi_ai_market_rate_proposer'::regrole)
  THEN RAISE EXCEPTION 'ai_market_v2_rate_proposal_unavailable'; END IF;
  value:=proposal_text::jsonb;
  IF jsonb_typeof(value) IS DISTINCT FROM 'object'
     OR proposal_text IS DISTINCT FROM public.ai_v4_replay_canonical(value)
     OR NOT value ?& ARRAY['schemaVersion','planId','executionReportId',
       'costLedgerId','ownerEmail','ownerVersion','modelId','modelVersion',
       'candidateDigest','tariffDigest','source','sourceDigest',
       'chargeCategories','sourceIndependentlyVerified',
       'fxIndependentlyVerified','allBillingCategoriesVerified','status',
       'providerCallsAllowed','rateProposalDigest']
     OR value-ARRAY['schemaVersion','planId','executionReportId',
       'costLedgerId','ownerEmail','ownerVersion','modelId','modelVersion',
       'candidateDigest','tariffDigest','source','sourceDigest',
       'chargeCategories','sourceIndependentlyVerified',
       'fxIndependentlyVerified','allBillingCategoriesVerified','status',
       'providerCallsAllowed','rateProposalDigest'] <> '{}'::jsonb
     OR value->>'schemaVersion' IS DISTINCT FROM
       'business-market-v2-source-rate-adoption-proposal-v1'
     OR value->>'planId' IS DISTINCT FROM selected_plan
     OR value->>'status' IS DISTINCT FROM
       'pending_independent_source_verification'
     OR value->'sourceIndependentlyVerified' IS DISTINCT FROM 'false'::jsonb
     OR value->'fxIndependentlyVerified' IS DISTINCT FROM 'false'::jsonb
     OR value->'allBillingCategoriesVerified' IS DISTINCT FROM 'false'::jsonb
     OR value->'providerCallsAllowed' IS DISTINCT FROM 'false'::jsonb
     OR value->>'rateProposalDigest' IS DISTINCT FROM encode(sha256(convert_to(
       public.ai_v4_replay_canonical(value-'rateProposalDigest'),'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_market_v2_rate_proposal_shape_invalid'; END IF;
  SELECT * INTO cost FROM public.ai_business_market_v2_cost_ledger_candidates item
    WHERE item.plan_id=selected_plan FOR SHARE;
  SELECT * INTO plan FROM public.ai_business_market_v2_execution_plans item
    WHERE item.id=selected_plan FOR SHARE;
  SELECT * INTO report FROM public.ai_report_runs item
    WHERE item.id=plan.execution_report_id FOR SHARE;
  SELECT * INTO actor FROM public.access_control_users item
    WHERE item.email=cost.owner_email FOR SHARE;
  SELECT * INTO model FROM public.ai_models item
    WHERE item.id=cost.model_id FOR SHARE;
  candidate:=cost.candidate_json::jsonb;
  root:=plan.plan_json::jsonb->'executionRoot';
  source:=value->'source';
  IF cost.id IS NULL OR plan.id IS NULL OR report.id IS NULL
     OR cost.reserved_cents<>0
     OR cost.status IS DISTINCT FROM 'pending_rate_and_approval_verification'
     OR cost.candidate_digest IS DISTINCT FROM encode(sha256(convert_to(
       cost.candidate_json,'UTF8')),'hex')
     OR plan.plan_digest IS DISTINCT FROM encode(sha256(convert_to(
       plan.plan_json,'UTF8')),'hex')
     OR actor.email IS NULL OR actor.role<>'admin' OR actor.status<>'active'
     OR actor.scope IS NOT NULL OR actor.email IS DISTINCT FROM cost.owner_email
     OR model.id IS NULL OR model.status<>'enabled'
     OR model.model_type<>'text' OR model.version IS DISTINCT FROM cost.model_version
     OR model.protocol IS DISTINCT FROM candidate->'model'->>'protocol'
     OR model.max_tokens IS DISTINCT FROM
       (candidate->'model'->>'maxTokens')::bigint
     OR model.max_tool_rounds IS DISTINCT FROM
       (candidate->'model'->>'maxToolRounds')::bigint
     OR model.max_total_tool_calls IS DISTINCT FROM
       (candidate->'model'->>'maxTotalToolCalls')::bigint
     OR value->>'costLedgerId' IS DISTINCT FROM cost.id
     OR value->>'executionReportId' IS DISTINCT FROM report.id
     OR value->>'ownerEmail' IS DISTINCT FROM cost.owner_email
     OR value->>'ownerVersion' IS DISTINCT FROM actor.version::text
     OR value->>'modelId' IS DISTINCT FROM cost.model_id
     OR value->>'modelVersion' IS DISTINCT FROM cost.model_version::text
     OR value->>'candidateDigest' IS DISTINCT FROM
       candidate->>'candidateDigest'
     OR value->>'tariffDigest' IS DISTINCT FROM cost.tariff_digest
     OR report.owner_email IS DISTINCT FROM cost.owner_email
     OR root->>'executionReportId' IS DISTINCT FROM report.id
     OR root->>'ownerEmail' IS DISTINCT FROM cost.owner_email
  THEN RAISE EXCEPTION 'ai_market_v2_rate_proposal_root_invalid'; END IF;
  IF jsonb_typeof(source) IS DISTINCT FROM 'object'
     OR NOT source ?& ARRAY['schemaVersion','providerId','modelId',
       'modelVersion','sourceCurrency','inputNanoPerMillionTokens',
       'outputNanoPerMillionTokens','cnyFxNumerator','cnyFxDenominator',
       'rateEvidenceDigest','fxEvidenceDigest','categoriesEvidenceDigest',
       'chargeCategories','effectiveAtUtc','expiresAtUtc']
     OR source-ARRAY['schemaVersion','providerId','modelId',
       'modelVersion','sourceCurrency','inputNanoPerMillionTokens',
       'outputNanoPerMillionTokens','cnyFxNumerator','cnyFxDenominator',
       'rateEvidenceDigest','fxEvidenceDigest','categoriesEvidenceDigest',
       'chargeCategories','effectiveAtUtc','expiresAtUtc'] <> '{}'::jsonb
     OR source->>'schemaVersion' IS DISTINCT FROM
       'business-market-v2-source-rate-rehearsal-v1'
     OR source->>'providerId' IS DISTINCT FROM
       candidate->'tariff'->>'providerId'
     OR source->>'modelId' IS DISTINCT FROM cost.model_id
     OR source->>'modelVersion' IS DISTINCT FROM cost.model_version::text
     OR source->>'sourceCurrency' IS NULL
     OR source->>'sourceCurrency' NOT IN ('CNY','USD')
     OR source->'chargeCategories' IS DISTINCT FROM
       '["input_tokens","output_tokens"]'::jsonb
     OR value->'chargeCategories' IS DISTINCT FROM
       '["input_tokens","output_tokens"]'::jsonb
     OR source->>'rateEvidenceDigest' IS DISTINCT FROM
       candidate->'tariff'->>'rateSourceDigest'
     OR source->>'rateEvidenceDigest' !~ '^[0-9a-f]{64}$'
     OR source->>'categoriesEvidenceDigest' IS NULL
     OR source->>'categoriesEvidenceDigest' !~ '^[0-9a-f]{64}$'
     OR source->>'inputNanoPerMillionTokens' IS NULL
     OR source->>'inputNanoPerMillionTokens' !~ '^[0-9]+$'
     OR source->>'outputNanoPerMillionTokens' IS NULL
     OR source->>'outputNanoPerMillionTokens' !~ '^[0-9]+$'
     OR source->>'cnyFxNumerator' IS NULL
     OR source->>'cnyFxNumerator' !~ '^[0-9]+$'
     OR source->>'cnyFxDenominator' IS NULL
     OR source->>'cnyFxDenominator' !~ '^[0-9]+$'
     OR source->>'effectiveAtUtc' IS DISTINCT FROM
       candidate->'tariff'->>'effectiveAtUtc'
     OR source->>'expiresAtUtc' IS DISTINCT FROM
       candidate->'tariff'->>'expiresAtUtc'
     OR value->>'sourceDigest' IS DISTINCT FROM encode(sha256(convert_to(
       public.ai_v4_replay_canonical(source),'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_market_v2_rate_source_invalid'; END IF;
  raw_in:=(source->>'inputNanoPerMillionTokens')::numeric;
  raw_out:=(source->>'outputNanoPerMillionTokens')::numeric;
  numerator:=(source->>'cnyFxNumerator')::numeric;
  denominator:=(source->>'cnyFxDenominator')::numeric;
  IF raw_in NOT BETWEEN 1 AND 1000000000000000
     OR raw_out NOT BETWEEN 1 AND 1000000000000000
     OR numerator NOT BETWEEN 1 AND 1000000000000000
     OR denominator NOT BETWEEN 1 AND 1000000000000000
     OR (source->>'sourceCurrency'='CNY' AND
       (numerator<>1 OR denominator<>1 OR
        source->'fxEvidenceDigest' IS DISTINCT FROM 'null'::jsonb))
     OR (source->>'sourceCurrency'='USD' AND
       (source->>'fxEvidenceDigest' IS NULL OR
        source->>'fxEvidenceDigest' !~ '^[0-9a-f]{64}$'))
     OR ceil(raw_in*numerator/denominator)::text IS DISTINCT FROM
       candidate->'tariff'->>'inputNanoYuanPerMillionTokens'
     OR ceil(raw_out*numerator/denominator)::text IS DISTINCT FROM
       candidate->'tariff'->>'outputNanoYuanPerMillionTokens'
     OR NOT ((source->>'effectiveAtUtc')::timestamptz<=clock_timestamp()
       AND clock_timestamp()<(source->>'expiresAtUtc')::timestamptz
       AND (source->>'expiresAtUtc')::timestamptz -
         (source->>'effectiveAtUtc')::timestamptz<=interval '31 days')
  THEN RAISE EXCEPTION 'ai_market_v2_rate_math_or_expiry_invalid'; END IF;
  row_id:=encode(sha256(convert_to('market-rate-proposal-v1|'||selected_plan,
    'UTF8')),'hex');
  checksum:=encode(sha256(convert_to(proposal_text,'UTF8')),'hex');
  INSERT INTO public.protected_business_market_v2_rate_proposals(
    id,plan_id,cost_ledger_id,report_id,owner_email,owner_version,
    model_id,model_version,proposal_json,proposal_digest,status,created_at)
  VALUES(row_id,selected_plan,cost.id,report.id,cost.owner_email,actor.version,
    model.id,model.version,proposal_text,checksum,
    'pending_independent_source_verification',clock_timestamp())
  ON CONFLICT(id) DO NOTHING;
  SELECT * INTO saved FROM public.protected_business_market_v2_rate_proposals item
    WHERE item.id=row_id;
  IF saved.id IS NULL OR saved.plan_id IS DISTINCT FROM selected_plan
     OR saved.proposal_json IS DISTINCT FROM proposal_text
     OR saved.proposal_digest IS DISTINCT FROM checksum
  THEN RAISE EXCEPTION 'ai_market_v2_rate_proposal_conflicting_replay'; END IF;
  RETURN row_id;
END $$"""

CAP_SQL = r"""CREATE FUNCTION public.ai_market_v2_record_cap_proposal(
  selected_rate text, proposal_text text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE value jsonb; approval jsonb; candidate jsonb;
  rate public.protected_business_market_v2_rate_proposals%ROWTYPE;
  cost public.ai_business_market_v2_cost_ledger_candidates%ROWTYPE;
  actor public.access_control_users%ROWTYPE;
  model public.ai_models%ROWTYPE;
  saved public.protected_business_market_v2_cap_proposals%ROWTYPE;
  row_id text; checksum text;
BEGIN
  IF session_user<>'teruisi_ai_market_cap_proposer'
     OR selected_rate IS NULL OR selected_rate !~ '^[0-9a-f]{64}$'
     OR proposal_text IS NULL OR octet_length(proposal_text) NOT BETWEEN 1 AND 16384
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT relowner FROM pg_catalog.pg_class WHERE
        oid='public.protected_business_market_v2_cap_proposals'::regclass))
     OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles role WHERE
       role.rolname='teruisi_ai_market_cap_proposer' AND NOT role.rolcanlogin
       AND NOT role.rolinherit AND NOT role.rolsuper AND NOT role.rolcreatedb
       AND NOT role.rolcreaterole AND NOT role.rolreplication
       AND NOT role.rolbypassrls)
     OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members member WHERE
       member.roleid='teruisi_ai_market_cap_proposer'::regrole
       OR member.member='teruisi_ai_market_cap_proposer'::regrole)
  THEN RAISE EXCEPTION 'ai_market_v2_cap_proposal_unavailable'; END IF;
  value:=proposal_text::jsonb;
  IF jsonb_typeof(value) IS DISTINCT FROM 'object'
     OR proposal_text IS DISTINCT FROM public.ai_v4_replay_canonical(value)
     OR NOT value ?& ARRAY['schemaVersion','planId','executionReportId',
       'costLedgerId','ownerEmail','ownerVersion','candidateDigest',
       'rateProposalDigest','approval','approvalDigest',
       'approvedCapClaimCents','humanApprovalVerified','status',
       'providerCallsAllowed','capProposalDigest']
     OR value-ARRAY['schemaVersion','planId','executionReportId',
       'costLedgerId','ownerEmail','ownerVersion','candidateDigest',
       'rateProposalDigest','approval','approvalDigest',
       'approvedCapClaimCents','humanApprovalVerified','status',
       'providerCallsAllowed','capProposalDigest'] <> '{}'::jsonb
     OR value->>'schemaVersion' IS DISTINCT FROM
       'business-market-v2-human-cap-adoption-proposal-v1'
     OR value->>'status' IS DISTINCT FROM 'pending_explicit_human_approval'
     OR value->'humanApprovalVerified' IS DISTINCT FROM 'false'::jsonb
     OR value->'providerCallsAllowed' IS DISTINCT FROM 'false'::jsonb
     OR value->>'capProposalDigest' IS DISTINCT FROM encode(sha256(convert_to(
       public.ai_v4_replay_canonical(value-'capProposalDigest'),'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_market_v2_cap_proposal_shape_invalid'; END IF;
  SELECT * INTO rate FROM public.protected_business_market_v2_rate_proposals item
    WHERE item.id=selected_rate FOR SHARE;
  SELECT * INTO cost FROM public.ai_business_market_v2_cost_ledger_candidates item
    WHERE item.id=rate.cost_ledger_id FOR SHARE;
  SELECT * INTO actor FROM public.access_control_users item
    WHERE item.email=rate.owner_email FOR SHARE;
  SELECT * INTO model FROM public.ai_models item
    WHERE item.id=rate.model_id FOR SHARE;
  candidate:=cost.candidate_json::jsonb;
  approval:=value->'approval';
  IF rate.id IS NULL OR cost.id IS NULL
     OR rate.status IS DISTINCT FROM 'pending_independent_source_verification'
     OR rate.proposal_digest IS DISTINCT FROM encode(sha256(convert_to(
       rate.proposal_json,'UTF8')),'hex')
     OR cost.candidate_digest IS DISTINCT FROM encode(sha256(convert_to(
       cost.candidate_json,'UTF8')),'hex')
     OR actor.email IS NULL OR actor.role<>'admin' OR actor.status<>'active'
     OR actor.scope IS NOT NULL OR actor.version IS DISTINCT FROM rate.owner_version
     OR model.id IS NULL OR model.status<>'enabled'
     OR model.version IS DISTINCT FROM rate.model_version
     OR model.protocol IS DISTINCT FROM candidate->'model'->>'protocol'
     OR model.max_tokens IS DISTINCT FROM
       (candidate->'model'->>'maxTokens')::bigint
     OR model.max_tool_rounds IS DISTINCT FROM
       (candidate->'model'->>'maxToolRounds')::bigint
     OR model.max_total_tool_calls IS DISTINCT FROM
       (candidate->'model'->>'maxTotalToolCalls')::bigint
     OR NOT ((rate.proposal_json::jsonb->'source'->>'effectiveAtUtc')::timestamptz
       <=clock_timestamp() AND clock_timestamp()<
       (rate.proposal_json::jsonb->'source'->>'expiresAtUtc')::timestamptz)
     OR EXISTS(SELECT 1 FROM public.protected_business_market_v2_authority_revocations
       rev WHERE rev.target_kind='rate' AND rev.target_id=rate.id)
     OR value->>'planId' IS DISTINCT FROM rate.plan_id
     OR value->>'executionReportId' IS DISTINCT FROM rate.report_id
     OR value->>'costLedgerId' IS DISTINCT FROM cost.id
     OR value->>'ownerEmail' IS DISTINCT FROM rate.owner_email
     OR value->>'ownerVersion' IS DISTINCT FROM rate.owner_version::text
     OR value->>'candidateDigest' IS DISTINCT FROM candidate->>'candidateDigest'
     OR value->>'rateProposalDigest' IS DISTINCT FROM
       rate.proposal_json::jsonb->>'rateProposalDigest'
     OR value->>'approvedCapClaimCents' IS DISTINCT FROM cost.cap_claim_cents::text
     OR cost.required_cents NOT BETWEEN 1 AND cost.cap_claim_cents
  THEN RAISE EXCEPTION 'ai_market_v2_cap_proposal_root_invalid'; END IF;
  IF jsonb_typeof(approval) IS DISTINCT FROM 'object'
     OR NOT approval ?& ARRAY['schemaVersion','planId','actorEmail',
       'approvedCapCents','approvalEvidenceDigest','approvedAtUtc','expiresAtUtc']
     OR approval-ARRAY['schemaVersion','planId','actorEmail',
       'approvedCapCents','approvalEvidenceDigest','approvedAtUtc','expiresAtUtc']
       <> '{}'::jsonb
     OR approval->>'schemaVersion' IS DISTINCT FROM
       'business-market-v2-cap-approval-rehearsal-v1'
     OR approval->>'planId' IS DISTINCT FROM rate.plan_id
     OR approval->>'actorEmail' IS DISTINCT FROM rate.owner_email
     OR approval->>'approvedCapCents' IS DISTINCT FROM cost.cap_claim_cents::text
     OR approval->>'approvalEvidenceDigest' IS DISTINCT FROM
       candidate->'envelope'->>'approvalDigest'
     OR approval->>'approvalEvidenceDigest' !~ '^[0-9a-f]{64}$'
     OR NOT ((approval->>'approvedAtUtc')::timestamptz<=clock_timestamp()
       AND clock_timestamp()<(approval->>'expiresAtUtc')::timestamptz
       AND (approval->>'expiresAtUtc')::timestamptz -
         (approval->>'approvedAtUtc')::timestamptz<=interval '31 days')
     OR value->>'approvalDigest' IS DISTINCT FROM encode(sha256(convert_to(
       public.ai_v4_replay_canonical(approval),'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_market_v2_cap_approval_invalid'; END IF;
  row_id:=encode(sha256(convert_to('market-cap-proposal-v1|'||selected_rate,
    'UTF8')),'hex');
  checksum:=encode(sha256(convert_to(proposal_text,'UTF8')),'hex');
  INSERT INTO public.protected_business_market_v2_cap_proposals(
    id,rate_id,plan_id,cost_ledger_id,report_id,owner_email,owner_version,
    cap_claim_cents,proposal_json,proposal_digest,status,created_at)
  VALUES(row_id,rate.id,rate.plan_id,cost.id,rate.report_id,rate.owner_email,
    rate.owner_version,cost.cap_claim_cents,proposal_text,checksum,
    'pending_explicit_human_approval',clock_timestamp())
  ON CONFLICT(id) DO NOTHING;
  SELECT * INTO saved FROM public.protected_business_market_v2_cap_proposals item
    WHERE item.id=row_id;
  IF saved.id IS NULL OR saved.rate_id IS DISTINCT FROM rate.id
     OR saved.proposal_json IS DISTINCT FROM proposal_text
     OR saved.proposal_digest IS DISTINCT FROM checksum
  THEN RAISE EXCEPTION 'ai_market_v2_cap_proposal_conflicting_replay'; END IF;
  RETURN row_id;
END $$"""

REVOKE_SQL = r"""CREATE FUNCTION public.ai_market_v2_revoke_authority_proposal(
  selected_kind text, selected_id text, reason_digest text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE row_id text; saved public.protected_business_market_v2_authority_revocations%ROWTYPE;
BEGIN
  IF session_user<>'teruisi_ai_market_proposal_revoker'
     OR selected_kind IS NULL
     OR selected_kind NOT IN ('rate','cap')
     OR selected_id IS NULL OR selected_id !~ '^[0-9a-f]{64}$'
     OR reason_digest IS NULL OR reason_digest !~ '^[0-9a-f]{64}$'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT relowner FROM pg_catalog.pg_class WHERE
        oid='public.protected_business_market_v2_authority_revocations'::regclass))
     OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles role WHERE
       role.rolname='teruisi_ai_market_proposal_revoker' AND NOT role.rolcanlogin
       AND NOT role.rolinherit AND NOT role.rolsuper AND NOT role.rolcreatedb
       AND NOT role.rolcreaterole AND NOT role.rolreplication
       AND NOT role.rolbypassrls)
     OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members member WHERE
       member.roleid='teruisi_ai_market_proposal_revoker'::regrole
       OR member.member='teruisi_ai_market_proposal_revoker'::regrole)
  THEN RAISE EXCEPTION 'ai_market_v2_authority_revoker_unavailable'; END IF;
  IF selected_kind='rate' THEN
    PERFORM 1 FROM public.protected_business_market_v2_rate_proposals item
      WHERE item.id=selected_id FOR UPDATE;
  ELSE
    PERFORM 1 FROM public.protected_business_market_v2_cap_proposals item
      WHERE item.id=selected_id FOR UPDATE;
  END IF;
  IF NOT FOUND THEN RAISE EXCEPTION 'ai_market_v2_revocation_target_missing'; END IF;
  row_id:=encode(sha256(convert_to('market-authority-revocation-v1|'||
    selected_kind||'|'||selected_id,'UTF8')),'hex');
  INSERT INTO public.protected_business_market_v2_authority_revocations(
    id,target_kind,target_id,reason_digest,created_at)
  VALUES(row_id,selected_kind,selected_id,reason_digest,clock_timestamp())
  ON CONFLICT(id) DO NOTHING;
  SELECT * INTO saved FROM public.protected_business_market_v2_authority_revocations
    item WHERE item.id=row_id;
  IF saved.id IS NULL OR saved.target_kind IS DISTINCT FROM selected_kind
     OR saved.target_id IS DISTINCT FROM selected_id
     OR saved.reason_digest IS DISTINCT FROM reason_digest
  THEN RAISE EXCEPTION 'ai_market_v2_revocation_conflicting_replay'; END IF;
  RETURN row_id;
END $$"""

READ_SQL = r"""CREATE FUNCTION public.ai_market_v2_authority_proposal_receipt(
  selected_plan text, selected_actor text, selected_version bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor public.access_control_users%ROWTYPE;
  rate public.protected_business_market_v2_rate_proposals%ROWTYPE;
  cap public.protected_business_market_v2_cap_proposals%ROWTYPE;
  rate_revoked boolean; cap_revoked boolean;
BEGIN
  IF session_user<>'teruisi_ai_reader'
     OR selected_plan IS NULL OR selected_plan !~ '^[0-9a-f]{64}$'
     OR selected_actor IS NULL OR selected_actor<>lower(selected_actor)
     OR selected_version IS NULL OR selected_version<1
  THEN RAISE EXCEPTION 'ai_market_v2_authority_reader_unavailable'; END IF;
  SELECT * INTO actor FROM public.access_control_users item
    WHERE item.email=selected_actor;
  SELECT * INTO rate FROM public.protected_business_market_v2_rate_proposals item
    WHERE item.plan_id=selected_plan;
  SELECT * INTO cap FROM public.protected_business_market_v2_cap_proposals item
    WHERE item.rate_id=rate.id;
  IF actor.email IS NULL OR actor.role<>'admin' OR actor.status<>'active'
     OR actor.scope IS NOT NULL OR actor.version IS DISTINCT FROM selected_version
     OR rate.id IS NULL OR rate.owner_email IS DISTINCT FROM actor.email
     OR rate.owner_version IS DISTINCT FROM actor.version
  THEN RAISE EXCEPTION 'ai_market_v2_authority_reader_mismatch'; END IF;
  SELECT EXISTS(SELECT 1 FROM public.protected_business_market_v2_authority_revocations
    item WHERE item.target_kind='rate' AND item.target_id=rate.id)
    INTO rate_revoked;
  SELECT EXISTS(SELECT 1 FROM public.protected_business_market_v2_authority_revocations
    item WHERE item.target_kind='cap' AND item.target_id=cap.id)
    INTO cap_revoked;
  RETURN jsonb_build_object('planId',selected_plan,'rateProposalId',rate.id,
    'rateProposalDigest',rate.proposal_json::jsonb->>'rateProposalDigest',
    'rateStatus',rate.status,'rateRevoked',rate_revoked,
    'capProposalId',cap.id,'capProposalDigest',
      cap.proposal_json::jsonb->>'capProposalDigest',
    'capStatus',cap.status,'capRevoked',cap_revoked,
    'tariffAuthorityVerified',false,'humanApprovalVerified',false,
    'providerCallsAllowed',false);
END $$"""

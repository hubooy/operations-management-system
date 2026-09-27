"""Test-only market paid-round ledger; no model dispatch permission.

0065 remains an immutable zero-reservation requirement. This migration adds
separate append-only rehearsal authority, reservation and dispatch-start rows.
No ordinary AI role can execute the mutation functions; even the NOLOGIN
roles are restricted to isolated PostgreSQL rehearsal databases and ports.
"""
import re

from django.db import migrations, models


ADOPTER = "teruisi_ai_market_paid_adopter"
RESERVER = "teruisi_ai_market_paid_reserver"
STARTER = "teruisi_ai_market_paid_starter"
READER = "teruisi_ai_reader"
WRITER = "teruisi_ai_writer"
AUTHORITY = "public.ai_business_market_v2_paid_authorities"
ROUNDS = "public.ai_business_market_v2_round_reservations"
EVENTS = "public.ai_business_market_v2_round_events"
EXPECTED_SIG = "public.ai_market_v2_paid_authority_expected(text,text)"
ADOPT_SIG = "public.ai_market_v2_adopt_paid_rehearsal(text,text)"
RESERVE_SIG = "public.ai_market_v2_reserve_paid_round(text,text,integer,text)"
START_SIG = "public.ai_market_v2_start_paid_dispatch(text,text)"

GUARD = r"""CREATE FUNCTION public.ai_market_v2_paid_row_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE expected_role text;
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'ai_market_v2_paid_row_immutable'; END IF;
  expected_role:=CASE TG_RELID
    WHEN 'public.ai_business_market_v2_paid_authorities'::regclass
      THEN 'teruisi_ai_market_paid_adopter'
    WHEN 'public.ai_business_market_v2_round_reservations'::regclass
      THEN 'teruisi_ai_market_paid_reserver'
    WHEN 'public.ai_business_market_v2_round_events'::regclass
      THEN 'teruisi_ai_market_paid_starter' ELSE NULL END;
  IF expected_role IS NULL OR session_user IS DISTINCT FROM expected_role
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT relowner FROM pg_catalog.pg_class WHERE oid=TG_RELID))
  THEN RAISE EXCEPTION 'ai_market_v2_paid_direct_write_denied'; END IF;
  RETURN NEW;
END $$"""

EXPECTED = r"""CREATE FUNCTION public.ai_market_v2_paid_authority_expected(
  selected_plan text, authority_text text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE value jsonb; source jsonb; approval jsonb; candidate jsonb;
  cost public.ai_business_market_v2_cost_ledger_candidates%ROWTYPE;
  plan public.ai_business_market_v2_execution_plans%ROWTYPE;
  actor public.access_control_users%ROWTYPE;
  raw_in numeric; raw_out numeric; fx_num numeric; fx_den numeric;
BEGIN
  IF selected_plan IS NULL OR selected_plan !~ '^[0-9a-f]{64}$'
     OR authority_text IS NULL OR octet_length(authority_text) NOT BETWEEN 1 AND 16384
  THEN RAISE EXCEPTION 'ai_market_v2_paid_authority_input_invalid'; END IF;
  value:=authority_text::jsonb;
  IF jsonb_typeof(value) IS DISTINCT FROM 'object'
     OR authority_text IS DISTINCT FROM public.ai_v4_replay_canonical(value)
     OR NOT value ?& ARRAY['schemaVersion','planId','candidateDigest',
       'tariffDigest','source','sourceDigest','approval','approvalDigest',
       'requiredCents','approvedCapCents','syntheticOnly',
       'authorityIndependentlyVerified','providerCallsAllowed','authorityDigest']
     OR value-ARRAY['schemaVersion','planId','candidateDigest',
       'tariffDigest','source','sourceDigest','approval','approvalDigest',
       'requiredCents','approvedCapCents','syntheticOnly',
       'authorityIndependentlyVerified','providerCallsAllowed','authorityDigest']
       <> '{}'::jsonb
     OR value->>'schemaVersion' IS DISTINCT FROM
       'business-market-v2-paid-authority-rehearsal-v1'
     OR value->>'planId' IS DISTINCT FROM selected_plan
     OR value->'syntheticOnly' IS DISTINCT FROM 'true'::jsonb
     OR value->'authorityIndependentlyVerified' IS DISTINCT FROM 'false'::jsonb
     OR value->'providerCallsAllowed' IS DISTINCT FROM 'false'::jsonb
     OR value->>'authorityDigest' IS DISTINCT FROM encode(sha256(convert_to(
       public.ai_v4_replay_canonical(value-'authorityDigest'),'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_market_v2_paid_authority_shape_invalid'; END IF;
  SELECT * INTO cost FROM public.ai_business_market_v2_cost_ledger_candidates item
    WHERE item.plan_id=selected_plan FOR SHARE;
  SELECT * INTO plan FROM public.ai_business_market_v2_execution_plans item
    WHERE item.id=selected_plan FOR SHARE;
  IF cost.id IS NULL OR plan.id IS NULL OR cost.reserved_cents<>0
     OR cost.status IS DISTINCT FROM 'pending_rate_and_approval_verification'
     OR cost.candidate_digest IS DISTINCT FROM encode(sha256(convert_to(
       cost.candidate_json,'UTF8')),'hex')
     OR plan.plan_digest IS DISTINCT FROM encode(sha256(convert_to(
       plan.plan_json,'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_market_v2_paid_cost_root_invalid'; END IF;
  candidate:=cost.candidate_json::jsonb;
  source:=value->'source'; approval:=value->'approval';
  IF value->>'candidateDigest' IS DISTINCT FROM candidate->>'candidateDigest'
     OR value->>'tariffDigest' IS DISTINCT FROM cost.tariff_digest
     OR value->>'requiredCents' IS DISTINCT FROM cost.required_cents::text
     OR value->>'approvedCapCents' IS DISTINCT FROM cost.cap_claim_cents::text
     OR cost.required_cents NOT BETWEEN 1 AND cost.cap_claim_cents
     OR value->>'sourceDigest' IS DISTINCT FROM encode(sha256(convert_to(
       public.ai_v4_replay_canonical(source),'UTF8')),'hex')
     OR value->>'approvalDigest' IS DISTINCT FROM encode(sha256(convert_to(
       public.ai_v4_replay_canonical(approval),'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_market_v2_paid_cost_binding_invalid'; END IF;
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
     OR source->>'providerId' IS DISTINCT FROM candidate->'tariff'->>'providerId'
     OR source->>'modelId' IS DISTINCT FROM cost.model_id
     OR source->>'modelVersion' IS DISTINCT FROM cost.model_version::text
     OR source->>'sourceCurrency' IS NULL
     OR source->>'sourceCurrency' NOT IN ('CNY','USD')
     OR source->'chargeCategories' IS DISTINCT FROM
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
  THEN RAISE EXCEPTION 'ai_market_v2_paid_rate_shape_invalid'; END IF;
  raw_in:=(source->>'inputNanoPerMillionTokens')::numeric;
  raw_out:=(source->>'outputNanoPerMillionTokens')::numeric;
  fx_num:=(source->>'cnyFxNumerator')::numeric;
  fx_den:=(source->>'cnyFxDenominator')::numeric;
  IF raw_in NOT BETWEEN 1 AND 1000000000000000
     OR raw_out NOT BETWEEN 1 AND 1000000000000000
     OR fx_num NOT BETWEEN 1 AND 1000000000000000
     OR fx_den NOT BETWEEN 1 AND 1000000000000000
     OR (source->>'sourceCurrency'='CNY' AND (fx_num<>1 OR fx_den<>1
       OR source->'fxEvidenceDigest' IS DISTINCT FROM 'null'::jsonb))
     OR (source->>'sourceCurrency'='USD' AND
       (source->>'fxEvidenceDigest' IS NULL OR
        source->>'fxEvidenceDigest' !~ '^[0-9a-f]{64}$'))
     OR ceil(raw_in*fx_num/fx_den)::text IS DISTINCT FROM
       candidate->'tariff'->>'inputNanoYuanPerMillionTokens'
     OR ceil(raw_out*fx_num/fx_den)::text IS DISTINCT FROM
       candidate->'tariff'->>'outputNanoYuanPerMillionTokens'
     OR NOT ((source->>'effectiveAtUtc')::timestamptz <= clock_timestamp()
       AND clock_timestamp() < (source->>'expiresAtUtc')::timestamptz
       AND (source->>'expiresAtUtc')::timestamptz -
         (source->>'effectiveAtUtc')::timestamptz <= interval '31 days')
  THEN RAISE EXCEPTION 'ai_market_v2_paid_rate_math_or_expiry_invalid'; END IF;
  IF jsonb_typeof(approval) IS DISTINCT FROM 'object'
     OR NOT approval ?& ARRAY['schemaVersion','planId','actorEmail',
       'approvedCapCents','approvalEvidenceDigest','approvedAtUtc','expiresAtUtc']
     OR approval-ARRAY['schemaVersion','planId','actorEmail',
       'approvedCapCents','approvalEvidenceDigest','approvedAtUtc','expiresAtUtc']
       <> '{}'::jsonb
     OR approval->>'schemaVersion' IS DISTINCT FROM
       'business-market-v2-cap-approval-rehearsal-v1'
     OR approval->>'planId' IS DISTINCT FROM selected_plan
     OR approval->>'actorEmail' IS DISTINCT FROM cost.owner_email
     OR approval->>'approvedCapCents' IS DISTINCT FROM cost.cap_claim_cents::text
     OR approval->>'approvalEvidenceDigest' IS DISTINCT FROM
       candidate->'envelope'->>'approvalDigest'
     OR approval->>'approvalEvidenceDigest' !~ '^[0-9a-f]{64}$'
     OR NOT ((approval->>'approvedAtUtc')::timestamptz <= clock_timestamp()
       AND clock_timestamp() < (approval->>'expiresAtUtc')::timestamptz
       AND (approval->>'expiresAtUtc')::timestamptz -
         (approval->>'approvedAtUtc')::timestamptz <= interval '31 days')
  THEN RAISE EXCEPTION 'ai_market_v2_paid_approval_shape_invalid'; END IF;
  SELECT * INTO actor FROM public.access_control_users item
    WHERE item.email=cost.owner_email FOR SHARE;
  IF actor.email IS NULL OR actor.role<>'admin' OR actor.status<>'active'
     OR actor.scope IS NOT NULL OR plan.plan_json::jsonb->'executionRoot'->>
       'ownerEmail' IS DISTINCT FROM cost.owner_email
  THEN RAISE EXCEPTION 'ai_market_v2_paid_owner_invalid'; END IF;
  RETURN value;
END $$"""

ADOPT = r"""CREATE FUNCTION public.ai_market_v2_adopt_paid_rehearsal(
  selected_plan text, authority_text text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE value jsonb; saved public.ai_business_market_v2_paid_authorities%ROWTYPE;
  row_id text; checksum text;
BEGIN
  IF session_user<>'teruisi_ai_market_paid_adopter'
     OR current_database() NOT IN ('teruisi_ai_rehearsal','test_teruisi_ai_rehearsal')
     OR inet_server_port() NOT BETWEEN 55440 AND 55999
     OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles role WHERE
       role.rolname='teruisi_ai_market_paid_adopter' AND NOT role.rolcanlogin
       AND NOT role.rolinherit AND NOT role.rolsuper AND NOT role.rolcreatedb
       AND NOT role.rolcreaterole AND NOT role.rolreplication
       AND NOT role.rolbypassrls)
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT relowner FROM pg_catalog.pg_class WHERE
        oid='public.ai_business_market_v2_paid_authorities'::regclass))
     OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members member WHERE
       member.roleid='teruisi_ai_market_paid_adopter'::regrole
       OR member.member='teruisi_ai_market_paid_adopter'::regrole)
  THEN RAISE EXCEPTION 'ai_market_v2_paid_rehearsal_unavailable'; END IF;
  value:=public.ai_market_v2_paid_authority_expected(selected_plan,authority_text);
  row_id:=encode(sha256(convert_to('market-paid-authority-v1|'||selected_plan,
    'UTF8')),'hex');
  checksum:=encode(sha256(convert_to(authority_text,'UTF8')),'hex');
  INSERT INTO public.ai_business_market_v2_paid_authorities(
    id,plan_id,cost_ledger_id,owner_email,authority_json,authority_digest,
    approved_cap_cents,status,created_at)
  SELECT row_id,selected_plan,cost.id,cost.owner_email,authority_text,checksum,
    (value->>'approvedCapCents')::bigint,'synthetic_rehearsal_only',clock_timestamp()
  FROM public.ai_business_market_v2_cost_ledger_candidates cost
  WHERE cost.plan_id=selected_plan
  ON CONFLICT(id) DO NOTHING;
  SELECT * INTO saved FROM public.ai_business_market_v2_paid_authorities item
    WHERE item.id=row_id;
  IF saved.id IS NULL OR saved.plan_id IS DISTINCT FROM selected_plan
     OR saved.authority_json IS DISTINCT FROM authority_text
     OR saved.authority_digest IS DISTINCT FROM checksum
     OR saved.status IS DISTINCT FROM 'synthetic_rehearsal_only'
  THEN RAISE EXCEPTION 'ai_market_v2_paid_authority_conflicting_replay'; END IF;
  RETURN row_id;
END $$"""

RESERVE = r"""CREATE FUNCTION public.ai_market_v2_reserve_paid_round(
  selected_authority text, selected_role text, selected_round integer,
  selected_request text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE authority public.ai_business_market_v2_paid_authorities%ROWTYPE;
  cost public.ai_business_market_v2_cost_ledger_candidates%ROWTYPE;
  saved public.ai_business_market_v2_round_reservations%ROWTYPE;
  configured public.ai_models%ROWTYPE; actor public.access_control_users%ROWTYPE;
  value jsonb; candidate jsonb; tariff jsonb; approval jsonb; rows jsonb;
  job jsonb; slot_id text; intent text; amount bigint; held bigint;
  expected_index integer;
BEGIN
  IF session_user<>'teruisi_ai_market_paid_reserver'
     OR current_database() NOT IN ('teruisi_ai_rehearsal','test_teruisi_ai_rehearsal')
     OR inet_server_port() NOT BETWEEN 55440 AND 55999
     OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles role WHERE
       role.rolname='teruisi_ai_market_paid_reserver' AND NOT role.rolcanlogin
       AND NOT role.rolinherit AND NOT role.rolsuper AND NOT role.rolcreatedb
       AND NOT role.rolcreaterole AND NOT role.rolreplication
       AND NOT role.rolbypassrls)
     OR selected_authority IS NULL OR selected_authority !~ '^[0-9a-f]{64}$'
     OR selected_role IS NULL
     OR selected_role NOT IN ('commerce','promotion','market_b2b',
       'independent_review','report')
     OR selected_round IS NULL OR selected_round<1 OR selected_round>20
     OR selected_request IS NULL OR selected_request !~ '^[0-9a-f]{64}$'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT relowner FROM pg_catalog.pg_class WHERE
        oid='public.ai_business_market_v2_round_reservations'::regclass))
     OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members member WHERE
       member.roleid='teruisi_ai_market_paid_reserver'::regrole
       OR member.member='teruisi_ai_market_paid_reserver'::regrole)
  THEN RAISE EXCEPTION 'ai_market_v2_paid_reservation_unavailable'; END IF;
  -- This row lock serializes all five roles under one approved cap.
  SELECT * INTO authority FROM public.ai_business_market_v2_paid_authorities item
    WHERE item.id=selected_authority FOR UPDATE;
  IF authority.id IS NULL OR authority.status<>'synthetic_rehearsal_only'
     OR authority.authority_digest IS DISTINCT FROM encode(sha256(convert_to(
       authority.authority_json,'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_market_v2_paid_authority_missing'; END IF;
  SELECT * INTO cost FROM public.ai_business_market_v2_cost_ledger_candidates item
    WHERE item.id=authority.cost_ledger_id FOR SHARE;
  SELECT * INTO actor FROM public.access_control_users item
    WHERE item.email=authority.owner_email FOR SHARE;
  value:=authority.authority_json::jsonb;
  candidate:=cost.candidate_json::jsonb;
  tariff:=candidate->'tariff'; approval:=value->'approval';
  SELECT item.* INTO configured FROM public.ai_models item
    WHERE item.id=cost.model_id FOR SHARE;
  IF cost.id IS NULL OR cost.plan_id IS DISTINCT FROM authority.plan_id
     OR cost.reserved_cents<>0
     OR cost.candidate_digest IS DISTINCT FROM encode(sha256(convert_to(
       cost.candidate_json,'UTF8')),'hex')
     OR candidate->>'candidateDigest' IS DISTINCT FROM value->>'candidateDigest'
     OR actor.email IS NULL OR actor.role<>'admin' OR actor.status<>'active'
     OR actor.scope IS NOT NULL OR actor.email IS DISTINCT FROM authority.owner_email
     OR configured.id IS NULL OR configured.status<>'enabled'
     OR configured.version IS DISTINCT FROM cost.model_version
     OR configured.model_type<>'text'
     OR authority.approved_cap_cents IS DISTINCT FROM cost.cap_claim_cents
     OR approval->>'approvedCapCents' IS DISTINCT FROM cost.cap_claim_cents::text
     OR NOT ((tariff->>'effectiveAtUtc')::timestamptz<=clock_timestamp()
       AND clock_timestamp()<(tariff->>'expiresAtUtc')::timestamptz)
     OR NOT ((approval->>'approvedAtUtc')::timestamptz<=clock_timestamp()
       AND clock_timestamp()<(approval->>'expiresAtUtc')::timestamptz)
  THEN RAISE EXCEPTION 'ai_market_v2_paid_live_authority_invalid'; END IF;
  rows:=candidate->'envelope'->'jobs';
  expected_index:=CASE selected_role WHEN 'commerce' THEN 0
    WHEN 'promotion' THEN 1 WHEN 'market_b2b' THEN 2
    WHEN 'independent_review' THEN 3 WHEN 'report' THEN 4 END;
  job:=rows->expected_index;
  IF job->>'role' IS DISTINCT FROM selected_role
     OR selected_round>(job->>'maxRounds')::integer
     -- No authenticated prior result/settlement path exists in this slice.
     OR selected_round<>1
  THEN RAISE EXCEPTION 'ai_market_v2_paid_round_not_admissible'; END IF;
  amount:=(job->>'maxCostCentsPerRound')::bigint;
  IF amount<1 OR amount>authority.approved_cap_cents
  THEN RAISE EXCEPTION 'ai_market_v2_paid_round_amount_invalid'; END IF;
  slot_id:=encode(sha256(convert_to(public.ai_v4_replay_canonical(
    jsonb_build_object('schemaVersion',
      'business-market-v2-round-reservation-quote-v1',
      'planId',authority.plan_id,'ledgerId',cost.id,
      'role',selected_role,'round',selected_round)),'UTF8')),'hex');
  intent:=encode(sha256(convert_to(public.ai_v4_replay_canonical(
    jsonb_build_object('slotId',slot_id,'requestDigest',selected_request,
      'maxCostCents',amount,
      'maxInputTokens',(job->>'maxInputTokensPerRound')::bigint,
      'maxOutputTokens',(job->>'maxOutputTokensPerRound')::bigint)),
      'UTF8')),'hex');
  SELECT * INTO saved FROM public.ai_business_market_v2_round_reservations item
    WHERE item.id=slot_id;
  IF saved.id IS NOT NULL THEN
    IF saved.authority_id IS DISTINCT FROM authority.id
       OR saved.intent_digest IS DISTINCT FROM intent
       OR saved.request_digest IS DISTINCT FROM selected_request
       OR saved.max_cost_cents IS DISTINCT FROM amount
    THEN RAISE EXCEPTION 'ai_market_v2_paid_round_conflicting_replay'; END IF;
    RETURN jsonb_build_object('slotId',slot_id,'intentDigest',intent,
      'maxCostCents',amount,'reservedCents',amount,
      'phase','reserved_or_dispatch_unknown','providerCallsAllowed',false);
  END IF;
  SELECT COALESCE(sum(item.max_cost_cents),0) INTO held
    FROM public.ai_business_market_v2_round_reservations item
    WHERE item.authority_id=authority.id;
  IF held+amount>authority.approved_cap_cents
  THEN RAISE EXCEPTION 'ai_market_v2_paid_cap_exceeded'; END IF;
  INSERT INTO public.ai_business_market_v2_round_reservations(
    id,authority_id,plan_id,role,round_number,request_digest,intent_digest,
    max_cost_cents,created_at)
  VALUES(slot_id,authority.id,authority.plan_id,selected_role,selected_round,
    selected_request,intent,amount,clock_timestamp());
  RETURN jsonb_build_object('slotId',slot_id,'intentDigest',intent,
    'maxCostCents',amount,'reservedCents',amount,
    'phase','reserved_awaiting_dispatch','providerCallsAllowed',false);
END $$"""

START = r"""CREATE FUNCTION public.ai_market_v2_start_paid_dispatch(
  selected_slot text, selected_intent text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE saved public.ai_business_market_v2_round_reservations%ROWTYPE;
  authority public.ai_business_market_v2_paid_authorities%ROWTYPE;
  actor public.access_control_users%ROWTYPE;
  configured public.ai_models%ROWTYPE;
  event_id text; value jsonb; candidate jsonb;
BEGIN
  IF session_user<>'teruisi_ai_market_paid_starter'
     OR current_database() NOT IN ('teruisi_ai_rehearsal','test_teruisi_ai_rehearsal')
     OR inet_server_port() NOT BETWEEN 55440 AND 55999
     OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles role WHERE
       role.rolname='teruisi_ai_market_paid_starter' AND NOT role.rolcanlogin
       AND NOT role.rolinherit AND NOT role.rolsuper AND NOT role.rolcreatedb
       AND NOT role.rolcreaterole AND NOT role.rolreplication
       AND NOT role.rolbypassrls)
     OR selected_slot IS NULL OR selected_slot !~ '^[0-9a-f]{64}$'
     OR selected_intent IS NULL OR selected_intent !~ '^[0-9a-f]{64}$'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT relowner FROM pg_catalog.pg_class WHERE
         oid='public.ai_business_market_v2_round_events'::regclass))
     OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members member WHERE
       member.roleid='teruisi_ai_market_paid_starter'::regrole
       OR member.member='teruisi_ai_market_paid_starter'::regrole)
  THEN RAISE EXCEPTION 'ai_market_v2_paid_dispatch_unavailable'; END IF;
  SELECT * INTO saved FROM public.ai_business_market_v2_round_reservations item
    WHERE item.id=selected_slot FOR UPDATE;
  IF saved.id IS NULL OR saved.intent_digest IS DISTINCT FROM selected_intent
  THEN RAISE EXCEPTION 'ai_market_v2_paid_dispatch_intent_mismatch'; END IF;
  SELECT * INTO authority FROM public.ai_business_market_v2_paid_authorities item
    WHERE item.id=saved.authority_id FOR SHARE;
  SELECT * INTO actor FROM public.access_control_users item
    WHERE item.email=authority.owner_email FOR SHARE;
  SELECT item.* INTO configured FROM public.ai_models item
    JOIN public.ai_business_market_v2_cost_ledger_candidates cost
      ON cost.model_id=item.id WHERE cost.id=authority.cost_ledger_id FOR SHARE;
  value:=authority.authority_json::jsonb;
  SELECT candidate_json::jsonb INTO candidate FROM
    public.ai_business_market_v2_cost_ledger_candidates item
    WHERE item.id=authority.cost_ledger_id;
  IF authority.id IS NULL OR authority.status<>'synthetic_rehearsal_only'
     OR actor.email IS NULL OR actor.role<>'admin' OR actor.status<>'active'
     OR actor.scope IS NOT NULL OR actor.email IS DISTINCT FROM authority.owner_email
     OR configured.id IS NULL OR configured.status<>'enabled'
     OR configured.version IS DISTINCT FROM
       (candidate->'model'->>'version')::bigint
     OR NOT ((value->'approval'->>'approvedAtUtc')::timestamptz<=clock_timestamp()
       AND clock_timestamp()<(value->'approval'->>'expiresAtUtc')::timestamptz)
     OR NOT ((candidate->'tariff'->>'effectiveAtUtc')::timestamptz<=clock_timestamp()
       AND clock_timestamp()<(candidate->'tariff'->>'expiresAtUtc')::timestamptz)
  THEN RAISE EXCEPTION 'ai_market_v2_paid_dispatch_authority_expired'; END IF;
  IF EXISTS(SELECT 1 FROM public.ai_business_market_v2_round_events item
    WHERE item.reservation_id=saved.id)
  THEN RAISE EXCEPTION 'ai_market_v2_paid_dispatch_unknown_no_retry'; END IF;
  event_id:=encode(sha256(convert_to('market-paid-dispatch-v1|'||saved.id,
    'UTF8')),'hex');
  INSERT INTO public.ai_business_market_v2_round_events(
    id,reservation_id,event_kind,intent_digest,created_at)
  VALUES(event_id,saved.id,'dispatch_started',selected_intent,clock_timestamp());
  -- Deliberately no network grant. A crash/timeout now remains unknown.
  RETURN jsonb_build_object('slotId',saved.id,'eventId',event_id,
    'phase','dispatch_outcome_unknown','providerCallsAllowed',false);
END $$"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        from ai_assistant.business_market_v2_cost_catalog import verify
        verify(cursor, RuntimeError)
        for role in (ADOPTER, RESERVER, STARTER):
            cursor.execute("SELECT to_regrole(%s)", [role])
            if cursor.fetchone()[0] is None:
                cursor.execute("CREATE ROLE " + role + " NOLOGIN NOINHERIT "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
            cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
                "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
                "WHERE rolname=%s", [role])
            if cursor.fetchone() != (False,) * 7:
                raise RuntimeError("0069 paid rehearsal role widened")
            cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
                "roleid=%s::regrole OR member=%s::regrole", [role, role])
            if cursor.fetchone() != (0,):
                raise RuntimeError("0069 paid rehearsal role membership drift")
            cursor.execute("GRANT USAGE ON SCHEMA public TO " + role)
        cursor.execute("""CREATE TABLE public.ai_business_market_v2_paid_authorities (
          id varchar(64) PRIMARY KEY,
          plan_id varchar(64) NOT NULL UNIQUE REFERENCES
            public.ai_business_market_v2_execution_plans(id) ON DELETE RESTRICT,
          cost_ledger_id varchar(64) NOT NULL UNIQUE REFERENCES
            public.ai_business_market_v2_cost_ledger_candidates(id) ON DELETE RESTRICT,
          owner_email varchar(320) NOT NULL,
          authority_json text NOT NULL,
          authority_digest varchar(64) NOT NULL,
          approved_cap_cents bigint NOT NULL CHECK (approved_cap_cents>0),
          status varchar(64) NOT NULL CHECK (status='synthetic_rehearsal_only'),
          created_at timestamptz NOT NULL
        )""")
        cursor.execute("""CREATE TABLE public.ai_business_market_v2_round_reservations (
          id varchar(64) PRIMARY KEY,
          authority_id varchar(64) NOT NULL REFERENCES
            public.ai_business_market_v2_paid_authorities(id) ON DELETE RESTRICT,
          plan_id varchar(64) NOT NULL REFERENCES
            public.ai_business_market_v2_execution_plans(id) ON DELETE RESTRICT,
          role varchar(64) NOT NULL CHECK (role IN ('commerce','promotion',
            'market_b2b','independent_review','report')),
          round_number integer NOT NULL CHECK (round_number BETWEEN 1 AND 20),
          request_digest varchar(64) NOT NULL,
          intent_digest varchar(64) NOT NULL,
          max_cost_cents bigint NOT NULL CHECK (max_cost_cents>0),
          created_at timestamptz NOT NULL,
          UNIQUE (plan_id,role,round_number)
        )""")
        cursor.execute("CREATE INDEX ai_market_v2_paid_held_idx ON " + ROUNDS +
            " (authority_id)")
        cursor.execute("""CREATE TABLE public.ai_business_market_v2_round_events (
          id varchar(64) PRIMARY KEY,
          reservation_id varchar(64) NOT NULL REFERENCES
            public.ai_business_market_v2_round_reservations(id) ON DELETE RESTRICT,
          event_kind varchar(32) NOT NULL CHECK (event_kind='dispatch_started'),
          intent_digest varchar(64) NOT NULL,
          created_at timestamptz NOT NULL,
          UNIQUE (reservation_id,event_kind)
        )""")
        for table in (AUTHORITY, ROUNDS, EVENTS):
            cursor.execute("REVOKE ALL ON " + table + " FROM PUBLIC")
            for role in (ADOPTER, RESERVER, STARTER, READER, WRITER):
                cursor.execute("SELECT to_regrole(%s)", [role])
                if cursor.fetchone()[0] is not None:
                    cursor.execute("REVOKE ALL ON " + table + " FROM " + role)
        for definition in (GUARD, EXPECTED, ADOPT, RESERVE, START):
            cursor.execute(definition)
        for signature in ("public.ai_market_v2_paid_row_guard()",
                EXPECTED_SIG, ADOPT_SIG, RESERVE_SIG, START_SIG):
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
            for role in (ADOPTER, RESERVER, STARTER, READER, WRITER):
                cursor.execute("SELECT to_regrole(%s)", [role])
                if cursor.fetchone()[0] is not None:
                    cursor.execute("REVOKE ALL ON FUNCTION " + signature+
                        " FROM " + role)
        for signature, role in ((ADOPT_SIG, ADOPTER),
                (RESERVE_SIG, RESERVER), (START_SIG, STARTER)):
            cursor.execute("GRANT EXECUTE ON FUNCTION " + signature + " TO " + role)
        for table, prefix in ((AUTHORITY, "authority"), (ROUNDS, "round"),
                              (EVENTS, "event")):
            cursor.execute("CREATE TRIGGER ai_market_v2_paid_"+prefix+
                "_guard BEFORE INSERT OR UPDATE OR DELETE ON "+table+
                " FOR EACH ROW EXECUTE FUNCTION public.ai_market_v2_paid_row_guard()")
            cursor.execute("CREATE TRIGGER ai_market_v2_paid_"+prefix+
                "_no_truncate BEFORE TRUNCATE ON "+table+
                " FOR EACH STATEMENT EXECUTE FUNCTION "
                "public.ai_v4_seal_ticket_no_truncate()")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table in (AUTHORITY, ROUNDS, EVENTS):
            cursor.execute("SELECT EXISTS(SELECT 1 FROM " + table + ")")
            if cursor.fetchone()[0]:
                raise RuntimeError("0069 cannot remove persisted paid rehearsal rows")
        for table, prefix in ((EVENTS, "event"), (ROUNDS, "round"),
                              (AUTHORITY, "authority")):
            for suffix in ("no_truncate", "guard"):
                cursor.execute("DROP TRIGGER ai_market_v2_paid_"+prefix+
                    "_"+suffix+" ON "+table)
        for signature in (START_SIG, RESERVE_SIG, ADOPT_SIG, EXPECTED_SIG,
                "public.ai_market_v2_paid_row_guard()"):
            cursor.execute("DROP FUNCTION " + signature)
        for table in (EVENTS, ROUNDS, AUTHORITY):
            cursor.execute("DROP TABLE " + table)
        # Keep the NOLOGIN roles, without executable functions.


def verify_catalog(cursor):
    """Fail readiness on role, SQL body, grant or table-guard drift."""
    for role in (ADOPTER, RESERVER, STARTER):
        cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname=%s", [role])
        if cursor.fetchone() != (False,) * 7:
            raise ValueError("market paid rehearsal role widened")
        cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
            "roleid=%s::regrole OR member=%s::regrole", [role, role])
        if cursor.fetchone() != (0,):
            raise ValueError("market paid rehearsal role membership drift")
    for table in (AUTHORITY, ROUNDS, EVENTS):
        cursor.execute("SELECT relkind,pg_catalog.pg_get_userbyid(relowner) "
            "FROM pg_catalog.pg_class WHERE oid=to_regclass(%s)", [table])
        result = cursor.fetchone()
        if result is None or result[0] != "r":
            raise ValueError("market paid rehearsal table missing")
        cursor.execute("SELECT attname,format_type(atttypid,atttypmod),attnotnull "
            "FROM pg_catalog.pg_attribute WHERE attrelid=to_regclass(%s) "
            "AND attnum>0 AND NOT attisdropped ORDER BY attnum", [table])
        expected_columns = {
            AUTHORITY: [("id", "character varying(64)"),
                ("plan_id", "character varying(64)"),
                ("cost_ledger_id", "character varying(64)"),
                ("owner_email", "character varying(320)"),
                ("authority_json", "text"), ("authority_digest", "character varying(64)"),
                ("approved_cap_cents", "bigint"),
                ("status", "character varying(64)"),
                ("created_at", "timestamp with time zone")],
            ROUNDS: [("id", "character varying(64)"),
                ("authority_id", "character varying(64)"),
                ("plan_id", "character varying(64)"),
                ("role", "character varying(64)"),
                ("round_number", "integer"),
                ("request_digest", "character varying(64)"),
                ("intent_digest", "character varying(64)"),
                ("max_cost_cents", "bigint"),
                ("created_at", "timestamp with time zone")],
            EVENTS: [("id", "character varying(64)"),
                ("reservation_id", "character varying(64)"),
                ("event_kind", "character varying(32)"),
                ("intent_digest", "character varying(64)"),
                ("created_at", "timestamp with time zone")],
        }[table]
        if cursor.fetchall() != [(name, kind, True)
                for name, kind in expected_columns]:
            raise ValueError("market paid rehearsal columns drift")
        for role in (ADOPTER, RESERVER, STARTER, READER, WRITER):
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE",
                              "TRUNCATE", "REFERENCES", "TRIGGER"):
                cursor.execute("SELECT has_table_privilege(%s,%s,%s)",
                    [role, table, privilege])
                if cursor.fetchone() != (False,):
                    raise ValueError("market paid rehearsal table ACL widened")
            for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
                cursor.execute("SELECT has_any_column_privilege(%s,%s,%s)",
                    [role, table, privilege])
                if cursor.fetchone() != (False,):
                    raise ValueError("market paid rehearsal column ACL widened")
        prefix = {AUTHORITY: "authority", ROUNDS: "round",
                  EVENTS: "event"}[table]
        cursor.execute("SELECT tgname,tgfoid::regprocedure::text,tgenabled,"
            "tgtype,tgqual IS NULL,tgdeferrable,tginitdeferred "
            "FROM pg_catalog.pg_trigger WHERE tgrelid=to_regclass(%s) "
            "AND NOT tgisinternal", [table])
        triggers = set(cursor.fetchall())
        cursor.execute("SELECT to_regprocedure(%s)::text", [
            "public.ai_market_v2_paid_row_guard()"])
        guard = cursor.fetchone()[0]
        cursor.execute("SELECT to_regprocedure(%s)::text", [
            "public.ai_v4_seal_ticket_no_truncate()"])
        no_truncate = cursor.fetchone()[0]
        if triggers != {
                ("ai_market_v2_paid_"+prefix+"_guard", guard, "O",
                    31, True, False, False),
                ("ai_market_v2_paid_"+prefix+"_no_truncate", no_truncate,
                    "O", 34, True, False, False)
                }:
            raise ValueError("market paid rehearsal triggers drift")
    expected_keys = {
        AUTHORITY: {"PRIMARY KEY (id)", "UNIQUE (plan_id)",
                    "UNIQUE (cost_ledger_id)"},
        ROUNDS: {"PRIMARY KEY (id)",
                 "UNIQUE (plan_id, role, round_number)"},
        EVENTS: {"PRIMARY KEY (id)",
                 "UNIQUE (reservation_id, event_kind)"}}
    expected_fk = {
        AUTHORITY: {"plan_id": "public.ai_business_market_v2_execution_plans",
                    "cost_ledger_id":
                        "public.ai_business_market_v2_cost_ledger_candidates"},
        ROUNDS: {"authority_id": AUTHORITY,
                 "plan_id": "public.ai_business_market_v2_execution_plans"},
        EVENTS: {"reservation_id": ROUNDS}}
    expected_checks = {AUTHORITY: {"approved_cap_cents", "status"},
                       ROUNDS: {"role", "round_number", "max_cost_cents"},
                       EVENTS: {"event_kind"}}
    for table in (AUTHORITY, ROUNDS, EVENTS):
        cursor.execute("SELECT contype,pg_catalog.pg_get_constraintdef(oid),"
            "confrelid::regclass::text,confdeltype,convalidated "
            "FROM pg_catalog.pg_constraint WHERE conrelid=to_regclass(%s)",
            [table])
        found = cursor.fetchall()
        if len(found) != (len(expected_keys[table]) +
                len(expected_fk[table]) + len(expected_checks[table])):
            raise ValueError("market paid rehearsal constraint count drift")
        keys = {definition for kind, definition, _, _, valid in found
                if kind in ("p", "u") and valid}
        if keys != expected_keys[table]:
            raise ValueError("market paid rehearsal key definition drift")
        seen_fk = set()
        for kind, definition, target, delete_action, valid in found:
            if kind != "f":
                continue
            match = re.match(r"FOREIGN KEY \(([^)]+)\) REFERENCES ", definition)
            column = match.group(1) if match else None
            wanted = expected_fk[table].get(column)
            cursor.execute("SELECT to_regclass(%s)::text", [wanted])
            if (wanted is None or target != cursor.fetchone()[0]
                    or delete_action != "r" or not valid
                    or "ON DELETE RESTRICT" not in definition
                    or column in seen_fk):
                raise ValueError("market paid rehearsal foreign key drift")
            seen_fk.add(column)
        if seen_fk != set(expected_fk[table]):
            raise ValueError("market paid rehearsal foreign key missing")
        seen_checks = set()
        for kind, definition, _, _, valid in found:
            if kind != "c":
                continue
            names = [name for name in expected_checks[table]
                     if re.search(r"\b" + name + r"\b", definition)]
            if len(names) != 1 or not valid:
                raise ValueError("market paid rehearsal CHECK drift")
            name = names[0]
            compact = re.sub(r"[\s()]", "", definition).lower()
            literals = set(re.findall(r"'([^']+)'", definition))
            if (name in seen_checks
                    or name in ("approved_cap_cents", "max_cost_cents")
                        and name+">0" not in compact
                    or name == "round_number" and not
                        ("round_number>=1" in compact and
                         "round_number<=20" in compact or
                         "round_numberbetween1and20" in compact)
                    or name == "status" and literals != {
                        "synthetic_rehearsal_only"}
                    or name == "event_kind" and literals != {
                        "dispatch_started"}
                    or name == "role" and literals != {"commerce",
                        "promotion", "market_b2b", "independent_review",
                        "report"}):
                raise ValueError("market paid rehearsal CHECK definition drift")
            seen_checks.add(name)
        if seen_checks != expected_checks[table]:
            raise ValueError("market paid rehearsal CHECK missing")
    cursor.execute("SELECT idx.indisunique,idx.indisvalid,idx.indisready,"
        "pg_catalog.pg_get_indexdef(idx.indexrelid),"
        "pg_catalog.pg_get_expr(idx.indpred,idx.indrelid) "
        "FROM pg_catalog.pg_index idx JOIN pg_catalog.pg_class rel "
        "ON rel.oid=idx.indexrelid WHERE rel.relname='ai_market_v2_paid_held_idx' "
        "AND idx.indrelid=to_regclass(%s)", [ROUNDS])
    held = cursor.fetchone()
    if (held is None or held[:3] != (False, True, True)
            or held[4] is not None
            or not held[3].endswith(" USING btree (authority_id)")):
        raise ValueError("market paid held index drift")
    cursor.execute("SELECT rel.relname FROM pg_catalog.pg_index idx "
        "JOIN pg_catalog.pg_class rel ON rel.oid=idx.indexrelid "
        "LEFT JOIN pg_catalog.pg_constraint con ON con.conindid=idx.indexrelid "
        "WHERE idx.indrelid=to_regclass(%s) AND con.oid IS NULL", [ROUNDS])
    if cursor.fetchall() != [("ai_market_v2_paid_held_idx",)]:
        raise ValueError("market paid extra standalone index")
    for signature, definition, definer, grant in (
            ("public.ai_market_v2_paid_row_guard()", GUARD, False, None),
            (EXPECTED_SIG, EXPECTED, True, None),
            (ADOPT_SIG, ADOPT, True, ADOPTER),
            (RESERVE_SIG, RESERVE, True, RESERVER),
            (START_SIG, START, True, STARTER)):
        cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,"
            "pg_catalog.pg_get_userbyid(p.proowner),l.lanname FROM "
            "pg_catalog.pg_proc p JOIN pg_catalog.pg_language l ON "
            "l.oid=p.prolang WHERE p.oid=to_regprocedure(%s)", [signature])
        row = cursor.fetchone()
        cursor.execute("SELECT pg_catalog.pg_get_userbyid(relowner) FROM "
            "pg_catalog.pg_class WHERE oid=to_regclass(%s)", [AUTHORITY])
        owner = cursor.fetchone()[0]
        if (row is None or row[0] != definition.split("$$", 2)[1]
                or row[1] is not definer or row[3] != owner
                or row[4] != "plpgsql"
                or {item.replace(" ", "") for item in (row[2] or [])}
                    != {"search_path=pg_catalog,public"}):
            raise ValueError("market paid rehearsal function drift")
        cursor.execute("SELECT pg_catalog.pg_get_userbyid(a.grantee),"
            "a.privilege_type FROM pg_catalog.pg_proc p,"
            "LATERAL pg_catalog.aclexplode(p.proacl) a "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        expected = {(owner, "EXECUTE")}
        if grant is not None:
            expected.add((grant, "EXECUTE"))
        if set(cursor.fetchall()) != expected:
            raise ValueError("market paid rehearsal function ACL widened")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant",
        "0068_business_promotion_budget_v11_verifier_receipt")]
    operations = [migrations.SeparateDatabaseAndState(
        database_operations=[], state_operations=[
            migrations.CreateModel(name="AiBusinessMarketV2PaidAuthority",fields=[
                ("id", models.CharField(max_length=64,primary_key=True,
                    serialize=False)),
                ("plan_id",models.CharField(max_length=64,unique=True)),
                ("cost_ledger_id",models.CharField(max_length=64,unique=True)),
                ("owner_email",models.CharField(max_length=320)),
                ("authority_json",models.TextField()),
                ("authority_digest",models.CharField(max_length=64)),
                ("approved_cap_cents",models.BigIntegerField()),
                ("status",models.CharField(max_length=64)),
                ("created_at",models.DateTimeField()),
            ],options={"db_table":"ai_business_market_v2_paid_authorities"}),
            migrations.CreateModel(name="AiBusinessMarketV2RoundReservation",fields=[
                ("id",models.CharField(max_length=64,primary_key=True,
                    serialize=False)),
                ("authority_id",models.CharField(max_length=64)),
                ("plan_id",models.CharField(max_length=64)),
                ("role",models.CharField(max_length=64)),
                ("round_number",models.IntegerField()),
                ("request_digest",models.CharField(max_length=64)),
                ("intent_digest",models.CharField(max_length=64)),
                ("max_cost_cents",models.BigIntegerField()),
                ("created_at",models.DateTimeField()),
            ],options={"db_table":"ai_business_market_v2_round_reservations"}),
            migrations.CreateModel(name="AiBusinessMarketV2RoundEvent",fields=[
                ("id",models.CharField(max_length=64,primary_key=True,
                    serialize=False)),
                ("reservation_id",models.CharField(max_length=64)),
                ("event_kind",models.CharField(max_length=32)),
                ("intent_digest",models.CharField(max_length=64)),
                ("created_at",models.DateTimeField()),
            ],options={"db_table":"ai_business_market_v2_round_events"}),
        ]), migrations.RunPython(install,uninstall)]

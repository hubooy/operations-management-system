"""SQL-owned cost requirement ledger; no tariff authority or funds reserved."""
from django.db import migrations, models


ROLE = "teruisi_ai_market_cost_attestor"
READER = "teruisi_ai_reader"
WRITER = "teruisi_ai_writer"
TABLE = "public.ai_business_market_v2_cost_ledger_candidates"
EXPECTED_SIGNATURE = "public.ai_market_v2_cost_candidate_expected(text,text)"
WRITE_SIGNATURE = "public.ai_market_v2_record_cost_candidate(text,text)"
READ_SIGNATURE = "public.ai_market_v2_cost_candidate_receipt(text,text,bigint)"

GUARD = r"""CREATE FUNCTION public.ai_market_v2_cost_candidate_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'ai_market_v2_cost_candidate_immutable'; END IF;
  IF session_user<>'teruisi_ai_market_cost_attestor'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT relowner FROM pg_catalog.pg_class WHERE oid=TG_RELID))
  THEN RAISE EXCEPTION 'ai_market_v2_cost_direct_write_denied'; END IF;
  RETURN NEW;
END $$"""

EXPECTED = r"""CREATE FUNCTION public.ai_market_v2_cost_candidate_expected(
  selected_plan text, candidate_text text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE saved_plan public.ai_business_market_v2_execution_plans%ROWTYPE;
  configured public.ai_models%ROWTYPE; value jsonb; plan jsonb;
  model jsonb; tariff jsonb; envelope jsonb; jobs jsonb; job jsonb;
  rate_in numeric; rate_out numeric; rounds bigint; input_tokens bigint;
  output_tokens bigint; one_round numeric; required_total numeric:=0;
  cap bigint; ordinal integer:=0; expected_role text; now_utc timestamptz;
  effective_at timestamptz; expires_at timestamptz;
BEGIN
  IF selected_plan IS NULL OR selected_plan !~ '^[0-9a-f]{64}$'
     OR candidate_text IS NULL OR octet_length(candidate_text) NOT BETWEEN 1 AND 65536
  THEN RAISE EXCEPTION 'ai_market_v2_cost_input_invalid'; END IF;
  value:=candidate_text::jsonb;
  IF jsonb_typeof(value) IS DISTINCT FROM 'object'
     OR candidate_text IS DISTINCT FROM public.ai_v4_replay_canonical(value)
     OR NOT value ?& ARRAY['schemaVersion','planId','model','modelDigest',
       'tariff','tariffDigest','envelope','envelopeDigest','requiredCents',
       'approvedCapClaimCents','reservedCents','status',
       'tariffAuthorityVerified','humanApprovalAuthorityVerified',
       'extraChargeCategoryCoverageVerified','currencyConversionVerified',
       'fundsReserved','providerCallsAllowed','candidateDigest']
     OR value-ARRAY['schemaVersion','planId','model','modelDigest',
       'tariff','tariffDigest','envelope','envelopeDigest','requiredCents',
       'approvedCapClaimCents','reservedCents','status',
       'tariffAuthorityVerified','humanApprovalAuthorityVerified',
       'extraChargeCategoryCoverageVerified','currencyConversionVerified',
       'fundsReserved','providerCallsAllowed','candidateDigest'] <> '{}'::jsonb
     OR value->>'candidateDigest' IS DISTINCT FROM encode(sha256(convert_to(
       public.ai_v4_replay_canonical(value-'candidateDigest'),'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_market_v2_cost_candidate_shape_invalid'; END IF;
  SELECT * INTO saved_plan FROM public.ai_business_market_v2_execution_plans item
    WHERE item.id=selected_plan FOR SHARE;
  IF saved_plan.id IS NULL
  THEN RAISE EXCEPTION 'ai_market_v2_cost_plan_missing'; END IF;
  plan:=public.ai_market_v2_execution_plan_expected(
    saved_plan.execution_report_id,saved_plan.plan_json);
  model:=value->'model'; tariff:=value->'tariff'; envelope:=value->'envelope';
  SELECT * INTO configured FROM public.ai_models item WHERE item.id=model->>'id' FOR SHARE;
  IF configured.id IS NULL OR configured.status IS DISTINCT FROM 'enabled'
     OR configured.model_type IS DISTINCT FROM 'text'
     OR configured.version IS DISTINCT FROM (model->>'version')::bigint
     OR configured.protocol IS DISTINCT FROM model->>'protocol'
     OR configured.max_tokens IS DISTINCT FROM (model->>'maxTokens')::bigint
     OR configured.max_tool_rounds IS DISTINCT FROM (model->>'maxToolRounds')::bigint
     OR configured.max_total_tool_calls IS DISTINCT FROM
       (model->>'maxTotalToolCalls')::bigint
     OR configured.max_total_tool_calls<5
     OR jsonb_typeof(model) IS DISTINCT FROM 'object'
     OR NOT model ?& ARRAY['id','version','status','modelType','protocol',
       'maxTokens','maxToolRounds','maxTotalToolCalls']
     OR model-ARRAY['id','version','status','modelType','protocol',
       'maxTokens','maxToolRounds','maxTotalToolCalls'] <> '{}'::jsonb
     OR value->>'modelDigest' IS DISTINCT FROM encode(sha256(convert_to(
       public.ai_v4_replay_canonical(model),'UTF8')),'hex')
     OR value->>'schemaVersion' IS DISTINCT FROM
       'business-market-v2-cost-ledger-candidate-v1'
     OR value->>'planId' IS DISTINCT FROM saved_plan.id
     OR value->>'status' IS DISTINCT FROM 'pending_rate_and_approval_verification'
     OR value->'reservedCents' IS DISTINCT FROM '0'::jsonb
     OR value->'tariffAuthorityVerified' IS DISTINCT FROM 'false'::jsonb
     OR value->'humanApprovalAuthorityVerified' IS DISTINCT FROM 'false'::jsonb
     OR value->'extraChargeCategoryCoverageVerified' IS DISTINCT FROM 'false'::jsonb
     OR value->'currencyConversionVerified' IS DISTINCT FROM 'false'::jsonb
     OR value->'fundsReserved' IS DISTINCT FROM 'false'::jsonb
     OR value->'providerCallsAllowed' IS DISTINCT FROM 'false'::jsonb
     OR saved_plan.plan_digest IS DISTINCT FROM encode(sha256(convert_to(
       saved_plan.plan_json,'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_market_v2_cost_model_or_authority_invalid'; END IF;
  IF jsonb_typeof(tariff) IS DISTINCT FROM 'object'
     OR NOT tariff ?& ARRAY['schemaVersion','providerId','modelId',
       'modelVersion','currency','inputNanoYuanPerMillionTokens',
       'outputNanoYuanPerMillionTokens','rateSourceDigest',
       'effectiveAtUtc','expiresAtUtc']
     OR tariff-ARRAY['schemaVersion','providerId','modelId',
       'modelVersion','currency','inputNanoYuanPerMillionTokens',
       'outputNanoYuanPerMillionTokens','rateSourceDigest',
       'effectiveAtUtc','expiresAtUtc'] <> '{}'::jsonb
     OR tariff->>'schemaVersion' IS DISTINCT FROM 'business-model-cny-tariff-candidate-v1'
     OR tariff->>'currency' IS DISTINCT FROM 'CNY'
     OR tariff->>'providerId' IS NULL
     OR tariff->>'providerId' !~ '^[A-Za-z0-9_.:-]{1,160}$'
     OR tariff->>'modelId' IS DISTINCT FROM configured.id
     OR tariff->>'modelVersion' IS DISTINCT FROM configured.version::text
     OR tariff->>'rateSourceDigest' IS NULL
     OR tariff->>'rateSourceDigest' !~ '^[0-9a-f]{64}$'
     OR tariff->>'inputNanoYuanPerMillionTokens' IS NULL
     OR tariff->>'inputNanoYuanPerMillionTokens' !~ '^[0-9]+$'
     OR tariff->>'outputNanoYuanPerMillionTokens' IS NULL
     OR tariff->>'outputNanoYuanPerMillionTokens' !~ '^[0-9]+$'
     OR tariff->>'effectiveAtUtc' IS NULL
     OR tariff->>'effectiveAtUtc' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
     OR tariff->>'expiresAtUtc' IS NULL
     OR tariff->>'expiresAtUtc' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
  THEN RAISE EXCEPTION 'ai_market_v2_cost_tariff_unverified'; END IF;
  rate_in:=(tariff->>'inputNanoYuanPerMillionTokens')::numeric;
  rate_out:=(tariff->>'outputNanoYuanPerMillionTokens')::numeric;
  IF rate_in NOT BETWEEN 1 AND 1000000000000000
     OR rate_out NOT BETWEEN 1 AND 1000000000000000
  THEN RAISE EXCEPTION 'ai_market_v2_cost_tariff_rate_invalid'; END IF;
  effective_at:=(tariff->>'effectiveAtUtc')::timestamptz;
  expires_at:=(tariff->>'expiresAtUtc')::timestamptz;
  now_utc:=clock_timestamp();
  IF NOT (effective_at<=now_utc AND now_utc<expires_at
          AND expires_at-effective_at<=interval '31 days')
  THEN RAISE EXCEPTION 'ai_market_v2_cost_tariff_expired'; END IF;
  IF value->>'tariffDigest' IS DISTINCT FROM encode(sha256(convert_to(
       public.ai_v4_replay_canonical(tariff),'UTF8')),'hex')
     OR value->>'tariffDigest' IS DISTINCT FROM envelope->>'tariffDigest'
     OR envelope->>'rateSourceDigest' IS DISTINCT FROM tariff->>'rateSourceDigest'
     OR envelope->>'providerId' IS DISTINCT FROM tariff->>'providerId'
     OR envelope->>'modelId' IS DISTINCT FROM configured.id
     OR envelope->>'modelVersion' IS DISTINCT FROM configured.version::text
     OR envelope->>'currency' IS DISTINCT FROM 'CNY'
  THEN RAISE EXCEPTION 'ai_market_v2_cost_tariff_binding_invalid'; END IF;
  IF jsonb_typeof(envelope) IS DISTINCT FROM 'object'
     OR NOT envelope ?& ARRAY['schemaVersion','providerId','modelId',
       'modelVersion','currency','tariffDigest','rateSourceDigest',
       'approvedCapCents','approvalDigest','jobs',
       'reservationRequiredCents','unreservedHeadroomCents',
       'tariffAuthorityVerified','humanApprovalAuthorityVerified',
       'providerUsageCategoryCoverageVerified',
       'fundsReservedInDurableLedger','providerCallsAllowed',
       'currencyConversionVerified','limitations','envelopeDigest']
     OR envelope-ARRAY['schemaVersion','providerId','modelId',
       'modelVersion','currency','tariffDigest','rateSourceDigest',
       'approvedCapCents','approvalDigest','jobs',
       'reservationRequiredCents','unreservedHeadroomCents',
       'tariffAuthorityVerified','humanApprovalAuthorityVerified',
       'providerUsageCategoryCoverageVerified',
       'fundsReservedInDurableLedger','providerCallsAllowed',
       'currencyConversionVerified','limitations','envelopeDigest'] <> '{}'::jsonb
     OR envelope->>'schemaVersion' IS DISTINCT FROM
       'business-market-five-agent-cost-envelope-candidate-v1'
     OR envelope->>'approvalDigest' IS NULL
     OR envelope->>'approvalDigest' !~ '^[0-9a-f]{64}$'
     OR envelope->'tariffAuthorityVerified' IS DISTINCT FROM 'false'::jsonb
     OR envelope->'humanApprovalAuthorityVerified' IS DISTINCT FROM 'false'::jsonb
     OR envelope->'providerUsageCategoryCoverageVerified' IS DISTINCT FROM 'false'::jsonb
     OR envelope->'fundsReservedInDurableLedger' IS DISTINCT FROM 'false'::jsonb
     OR envelope->'providerCallsAllowed' IS DISTINCT FROM 'false'::jsonb
     OR envelope->'currencyConversionVerified' IS DISTINCT FROM 'false'::jsonb
     OR jsonb_typeof(envelope->'jobs') IS DISTINCT FROM 'array'
     OR jsonb_array_length(envelope->'jobs')<>5
     OR envelope->>'envelopeDigest' IS DISTINCT FROM encode(sha256(convert_to(
       public.ai_v4_replay_canonical(envelope-'envelopeDigest'),'UTF8')),'hex')
     OR value->>'envelopeDigest' IS DISTINCT FROM envelope->>'envelopeDigest'
  THEN RAISE EXCEPTION 'ai_market_v2_cost_envelope_invalid'; END IF;
  IF value->>'approvedCapClaimCents' IS NULL
     OR value->>'approvedCapClaimCents' !~ '^[0-9]+$'
  THEN RAISE EXCEPTION 'ai_market_v2_cost_cap_claim_invalid'; END IF;
  cap:=(value->>'approvedCapClaimCents')::bigint;
  IF cap NOT BETWEEN 1 AND 100000000
     OR envelope->>'approvedCapCents' IS DISTINCT FROM cap::text
  THEN RAISE EXCEPTION 'ai_market_v2_cost_cap_claim_invalid'; END IF;
  jobs:=envelope->'jobs';
  FOR job IN SELECT jsonb_array_elements(jobs) LOOP
    ordinal:=ordinal+1;
    expected_role:=CASE ordinal WHEN 1 THEN 'commerce'
      WHEN 2 THEN 'promotion' WHEN 3 THEN 'market_b2b'
      WHEN 4 THEN 'independent_review' WHEN 5 THEN 'report' ELSE NULL END;
    IF jsonb_typeof(job) IS DISTINCT FROM 'object'
       OR NOT job ?& ARRAY['role','maxRounds','maxInputTokensPerRound',
         'maxOutputTokensPerRound','maxCostCentsPerRound','reservationCents']
       OR job-ARRAY['role','maxRounds','maxInputTokensPerRound',
         'maxOutputTokensPerRound','maxCostCentsPerRound','reservationCents']
         <> '{}'::jsonb
       OR job->>'role' IS DISTINCT FROM expected_role
       OR job->>'maxRounds' IS NULL OR job->>'maxRounds' !~ '^[0-9]+$'
       OR job->>'maxInputTokensPerRound' IS NULL
       OR job->>'maxInputTokensPerRound' !~ '^[0-9]+$'
       OR job->>'maxOutputTokensPerRound' IS NULL
       OR job->>'maxOutputTokensPerRound' !~ '^[0-9]+$'
    THEN RAISE EXCEPTION 'ai_market_v2_cost_job_shape_invalid'; END IF;
    rounds:=(job->>'maxRounds')::bigint;
    input_tokens:=(job->>'maxInputTokensPerRound')::bigint;
    output_tokens:=(job->>'maxOutputTokensPerRound')::bigint;
    IF rounds NOT BETWEEN 1 AND 20
       OR rounds>configured.max_tool_rounds
       OR input_tokens NOT BETWEEN 1 AND 1000000
       OR output_tokens NOT BETWEEN 1 AND 128000
       OR output_tokens>configured.max_tokens
    THEN RAISE EXCEPTION 'ai_market_v2_cost_job_limits_invalid'; END IF;
    one_round:=ceil((input_tokens::numeric*rate_in+
      output_tokens::numeric*rate_out)/10000000000000::numeric);
    IF one_round NOT BETWEEN 1 AND 100000000
       OR job->>'maxCostCentsPerRound' IS DISTINCT FROM one_round::bigint::text
       OR job->>'reservationCents' IS DISTINCT FROM
          (one_round*rounds)::bigint::text
    THEN RAISE EXCEPTION 'ai_market_v2_cost_job_math_invalid'; END IF;
    required_total:=required_total+one_round*rounds;
    IF required_total>cap
    THEN RAISE EXCEPTION 'ai_market_v2_cost_exceeds_claimed_cap'; END IF;
  END LOOP;
  IF required_total<1 OR required_total>100000000
     OR value->>'requiredCents' IS DISTINCT FROM required_total::bigint::text
     OR envelope->>'reservationRequiredCents' IS DISTINCT FROM
       required_total::bigint::text
     OR envelope->>'unreservedHeadroomCents' IS DISTINCT FROM
       (cap-required_total)::bigint::text
  THEN RAISE EXCEPTION 'ai_market_v2_cost_total_mismatch'; END IF;
  RETURN value;
END $$"""

WRITE = r"""CREATE FUNCTION public.ai_market_v2_record_cost_candidate(
  selected_plan text, candidate_text text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE value jsonb; saved public.ai_business_market_v2_cost_ledger_candidates%ROWTYPE;
  plan_id text; row_id text; checksum text;
BEGIN
  IF session_user<>'teruisi_ai_market_cost_attestor'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT relowner FROM pg_catalog.pg_class WHERE
         oid='public.ai_business_market_v2_cost_ledger_candidates'::regclass))
     OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles role WHERE
       role.rolname='teruisi_ai_market_cost_attestor' AND NOT role.rolcanlogin
       AND NOT role.rolinherit AND NOT role.rolsuper AND NOT role.rolcreatedb
       AND NOT role.rolcreaterole AND NOT role.rolreplication
       AND NOT role.rolbypassrls)
     OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members member WHERE
       member.roleid='teruisi_ai_market_cost_attestor'::regrole
       OR member.member='teruisi_ai_market_cost_attestor'::regrole)
  THEN RAISE EXCEPTION 'ai_market_v2_cost_attestor_unavailable'; END IF;
  value:=public.ai_market_v2_cost_candidate_expected(selected_plan,candidate_text);
  row_id:=encode(sha256(convert_to('market-cost-v1|'||selected_plan,'UTF8')),'hex');
  checksum:=encode(sha256(convert_to(candidate_text,'UTF8')),'hex');
  INSERT INTO public.ai_business_market_v2_cost_ledger_candidates(
    id,plan_id,owner_email,model_id,model_version,tariff_digest,
    envelope_digest,candidate_json,candidate_digest,required_cents,
    cap_claim_cents,reserved_cents,status,created_at)
  VALUES(row_id,selected_plan,
    (SELECT plan_json::jsonb->'executionRoot'->>'ownerEmail' FROM
       public.ai_business_market_v2_execution_plans WHERE id=selected_plan),
    value->'model'->>'id',(value->'model'->>'version')::bigint,
    value->>'tariffDigest',value->>'envelopeDigest',candidate_text,checksum,
    (value->>'requiredCents')::bigint,
    (value->>'approvedCapClaimCents')::bigint,0,
    'pending_rate_and_approval_verification',clock_timestamp())
  ON CONFLICT(id) DO NOTHING;
  SELECT * INTO saved FROM public.ai_business_market_v2_cost_ledger_candidates item
    WHERE item.id=row_id;
  IF saved.id IS NULL OR saved.plan_id IS DISTINCT FROM selected_plan
     OR saved.candidate_json IS DISTINCT FROM candidate_text
     OR saved.candidate_digest IS DISTINCT FROM checksum
     OR saved.required_cents IS DISTINCT FROM (value->>'requiredCents')::bigint
     OR saved.reserved_cents IS DISTINCT FROM 0
     OR saved.status IS DISTINCT FROM 'pending_rate_and_approval_verification'
  THEN RAISE EXCEPTION 'ai_market_v2_cost_candidate_conflicting_replay'; END IF;
  RETURN row_id;
END $$"""

READ = r"""CREATE FUNCTION public.ai_market_v2_cost_candidate_receipt(
  selected_plan text, selected_actor text, selected_version bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor public.access_control_users%ROWTYPE;
  saved public.ai_business_market_v2_cost_ledger_candidates%ROWTYPE;
  value jsonb;
BEGIN
  IF session_user<>'teruisi_ai_reader'
     OR selected_actor IS NULL OR selected_actor<>lower(selected_actor)
     OR selected_version IS NULL OR selected_version<1
  THEN RAISE EXCEPTION 'ai_market_v2_cost_reader_unavailable'; END IF;
  SELECT * INTO actor FROM public.access_control_users item
    WHERE item.email=selected_actor;
  SELECT * INTO saved FROM public.ai_business_market_v2_cost_ledger_candidates item
    WHERE item.plan_id=selected_plan;
  IF saved.id IS NULL
  THEN RAISE EXCEPTION 'ai_market_v2_cost_candidate_missing'; END IF;
  value:=public.ai_market_v2_cost_candidate_expected(
    selected_plan,saved.candidate_json);
  IF actor.email IS NULL OR actor.version IS DISTINCT FROM selected_version
     OR actor.role IS DISTINCT FROM 'admin' OR actor.status IS DISTINCT FROM 'active'
     OR actor.scope IS NOT NULL OR saved.owner_email IS DISTINCT FROM actor.email
     OR saved.candidate_digest IS DISTINCT FROM encode(sha256(convert_to(
       saved.candidate_json,'UTF8')),'hex')
     OR saved.reserved_cents IS DISTINCT FROM 0
     OR saved.status IS DISTINCT FROM 'pending_rate_and_approval_verification'
  THEN RAISE EXCEPTION 'ai_market_v2_cost_receipt_mismatch'; END IF;
  RETURN value||jsonb_build_object('ledgerId',saved.id,
    'ledgerRequiredCents',saved.required_cents,
    'ledgerReservedCents',saved.reserved_cents,
    'providerCallsAllowed',false);
END $$"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        from ai_assistant.business_market_v2_execution_plan_catalog import verify
        verify(cursor, RuntimeError)
        from ai_assistant.business_market_v2_synthetic_catalog import verify as verify_synthetic
        verify_synthetic(cursor, RuntimeError)
        cursor.execute("SELECT to_regrole(%s)",[ROLE])
        if cursor.fetchone()[0] is None:
            cursor.execute("CREATE ROLE " + ROLE + " NOLOGIN NOINHERIT NOSUPERUSER "
                "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
        cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname=%s",[ROLE])
        if cursor.fetchone() != (False,)*7:
            raise RuntimeError("0065 cost attestor must remain NOLOGIN")
        cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
            "roleid=%s::regrole OR member=%s::regrole",[ROLE,ROLE])
        if cursor.fetchone() != (0,):
            raise RuntimeError("0065 cost attestor membership drift")
        cursor.execute("""CREATE TABLE public.ai_business_market_v2_cost_ledger_candidates (
          id varchar(64) PRIMARY KEY,
          plan_id varchar(64) NOT NULL UNIQUE REFERENCES
            public.ai_business_market_v2_execution_plans(id) ON DELETE RESTRICT,
          owner_email varchar(320) NOT NULL,
          model_id varchar(160) NOT NULL,
          model_version bigint NOT NULL,
          tariff_digest varchar(64) NOT NULL,
          envelope_digest varchar(64) NOT NULL,
          candidate_json text NOT NULL,
          candidate_digest varchar(64) NOT NULL,
          required_cents bigint NOT NULL,
          cap_claim_cents bigint NOT NULL,
          reserved_cents bigint NOT NULL CHECK (reserved_cents=0),
          status varchar(64) NOT NULL CHECK (
            status='pending_rate_and_approval_verification'),
          created_at timestamptz NOT NULL
        )""")
        cursor.execute("REVOKE ALL ON " + TABLE + " FROM PUBLIC")
        cursor.execute("GRANT USAGE ON SCHEMA public TO " + ROLE)
        for account in (READER,WRITER,ROLE):
            cursor.execute("SELECT to_regrole(%s)",[account])
            if cursor.fetchone()[0] is not None:
                cursor.execute("REVOKE ALL ON " + TABLE + " FROM " + account)
        for definition in (GUARD,EXPECTED,WRITE,READ):
            cursor.execute(definition)
        for signature in ("public.ai_market_v2_cost_candidate_guard()",
                EXPECTED_SIGNATURE,WRITE_SIGNATURE,READ_SIGNATURE):
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
            for account in (ROLE,READER,WRITER):
                cursor.execute("SELECT to_regrole(%s)",[account])
                if cursor.fetchone()[0] is not None:
                    cursor.execute("REVOKE ALL ON FUNCTION " + signature+
                        " FROM " + account)
        cursor.execute("GRANT EXECUTE ON FUNCTION " + WRITE_SIGNATURE + " TO " + ROLE)
        cursor.execute("SELECT to_regrole(%s)",[READER])
        if cursor.fetchone()[0] is not None:
            cursor.execute("GRANT EXECUTE ON FUNCTION " + READ_SIGNATURE + " TO " + READER)
        cursor.execute("CREATE TRIGGER ai_market_v2_cost_candidate_guard BEFORE "
            "INSERT OR UPDATE OR DELETE ON " + TABLE + " FOR EACH ROW "
            "EXECUTE FUNCTION public.ai_market_v2_cost_candidate_guard()")
        cursor.execute("CREATE TRIGGER ai_market_v2_cost_candidate_no_truncate "
            "BEFORE TRUNCATE ON " + TABLE + " FOR EACH STATEMENT "
            "EXECUTE FUNCTION public.ai_v4_seal_ticket_no_truncate()")


def uninstall(apps,schema_editor):
    if schema_editor.connection.vendor!="postgresql":return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM " + TABLE + ")")
        if cursor.fetchone()[0]:
            raise RuntimeError("0065 cannot remove persisted cost candidates")
        for name in ("ai_market_v2_cost_candidate_no_truncate",
                "ai_market_v2_cost_candidate_guard"):
            cursor.execute("DROP TRIGGER " + name + " ON " + TABLE)
        for signature in (READ_SIGNATURE,WRITE_SIGNATURE,EXPECTED_SIGNATURE,
                "public.ai_market_v2_cost_candidate_guard()"):
            cursor.execute("DROP FUNCTION " + signature)
        cursor.execute("DROP TABLE " + TABLE)
        # Retain the NOLOGIN role without an executable function grant.


class Migration(migrations.Migration):
    dependencies=[("ai_assistant","0064_business_market_v2_synthetic_vertical")]
    operations=[
        migrations.SeparateDatabaseAndState(database_operations=[],state_operations=[
            migrations.CreateModel(name="AiBusinessMarketV2CostLedgerCandidate",fields=[
                ("id",models.CharField(max_length=64,primary_key=True,serialize=False)),
                ("plan_id",models.CharField(max_length=64,unique=True)),
                ("owner_email",models.CharField(max_length=320)),
                ("model_id",models.CharField(max_length=160)),
                ("model_version",models.BigIntegerField()),
                ("tariff_digest",models.CharField(max_length=64)),
                ("envelope_digest",models.CharField(max_length=64)),
                ("candidate_json",models.TextField()),
                ("candidate_digest",models.CharField(max_length=64)),
                ("required_cents",models.BigIntegerField()),
                ("cap_claim_cents",models.BigIntegerField()),
                ("reserved_cents",models.BigIntegerField()),
                ("status",models.CharField(max_length=64)),
                ("created_at",models.DateTimeField()),
            ],options={"db_table":"ai_business_market_v2_cost_ledger_candidates"}),
        ]),
        migrations.RunPython(install,uninstall),
    ]

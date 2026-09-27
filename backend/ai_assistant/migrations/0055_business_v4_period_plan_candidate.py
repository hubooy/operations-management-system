"""Append-only, non-authorizing v4 three-window date-envelope sidecar."""
from importlib import import_module
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


TABLE = "public.ai_business_v4_period_plan_candidates"
WRITE = "public.ai_v4_record_period_plan_candidate(text,text,text,bigint,text,text)"

GUARD = """CREATE FUNCTION public.ai_v4_period_plan_candidate_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'ai_v4_period_candidate_immutable'; END IF;
  IF session_user<>'teruisi_ai_writer' OR current_user IS DISTINCT FROM
    pg_catalog.pg_get_userbyid((SELECT relowner FROM pg_catalog.pg_class
      WHERE oid=TG_RELID))
  THEN RAISE EXCEPTION 'ai_v4_period_candidate_direct_write_denied'; END IF;
  RETURN NEW;
END $$"""

NO_TRUNCATE = """CREATE FUNCTION public.ai_v4_period_plan_no_truncate() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_class c
       WHERE c.oid=TG_RELID AND
         pg_catalog.pg_get_userbyid(c.relowner)=session_user)
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_roles r
       WHERE r.rolname=session_user AND r.rolsuper)
  THEN RETURN NULL; END IF;
  RAISE EXCEPTION 'ai_v4_period_candidate_truncate_denied';
END $$"""

RECORD = """CREATE FUNCTION public.ai_v4_record_period_plan_candidate(
  selected_run text,selected_attempt text,selected_actor text,
  selected_actor_version bigint,canonical_envelope text,
  selected_envelope_digest text)
RETURNS TABLE(envelope_digest text,source_root text,created_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_v4_runs%ROWTYPE;
  attempt public.ai_business_v4_validation_attempts%ROWTYPE;
  source public.ai_business_v4_sources%ROWTYPE;
  saved public.ai_business_v4_period_plan_candidates%ROWTYPE;
  body jsonb; plan jsonb; entry jsonb; query jsonb; item jsonb;
  finance_item jsonb; expected_finance jsonb; expected_item jsonb;
  expected_periods jsonb; expected_period jsonb; requested_period jsonb;
  expected_policy jsonb; expected_days jsonb;
  first_day date; last_day date; window_first date; window_last date;
  last_year_month_end date; first_year_month_end date;
  days integer; daily_count integer:=0; finance_count integer:=0;
  root text; count_sources bigint; guard_text text; inserted_count integer;
BEGIN
  IF session_user<>'teruisi_ai_writer'
     OR selected_envelope_digest !~ '^[0-9a-f]{64}$'
     OR canonical_envelope IS NULL OR octet_length(canonical_envelope)>65536
     OR selected_actor_version IS NULL OR selected_actor_version<1
  THEN RAISE EXCEPTION 'ai_v4_period_candidate_input_invalid'; END IF;
  IF current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT relowner FROM pg_catalog.pg_class
        WHERE oid='public.ai_business_v4_period_plan_candidates'::regclass))
     OR has_table_privilege('teruisi_ai_writer',
       'public.ai_business_v4_period_plan_candidates',
       'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
  THEN RAISE EXCEPTION 'ai_v4_period_candidate_privileges_invalid'; END IF;
  SELECT probe.guard_version INTO guard_text FROM
    public.ai_v4_lock_source_revisions_for_admission() probe;
  IF guard_text IS DISTINCT FROM 'business-v4-source-write-fence-read-v1'
  THEN RAISE EXCEPTION 'ai_v4_period_candidate_source_fence_missing'; END IF;
  SELECT * INTO parent FROM public.ai_business_v4_runs r
    WHERE r.id=selected_run FOR UPDATE;
  SELECT * INTO attempt FROM public.ai_business_v4_validation_attempts a
    WHERE a.id=selected_attempt;
  IF parent.id IS NULL OR attempt.id IS NULL
     OR parent.status<>'collecting' OR parent.collection_status<>'manual'
     OR parent.scope_json<>'null' OR parent.owner_email<>selected_actor
     OR parent.plan_digest IS DISTINCT FROM
       encode(sha256(convert_to(parent.plan_json,'UTF8')),'hex')
     OR attempt.run_id<>parent.id OR attempt.run_version<>parent.version
     OR attempt.plan_digest<>parent.plan_digest
     OR attempt.actor_email<>selected_actor
     OR attempt.actor_version<>selected_actor_version
     OR (SELECT latest.id FROM public.ai_business_v4_validation_attempts latest
       WHERE latest.run_id=parent.id ORDER BY latest.created_at DESC,latest.id DESC
       LIMIT 1) IS DISTINCT FROM attempt.id
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users account
       WHERE account.email=selected_actor AND account.role='admin'
         AND account.status='active' AND account.scope IS NULL
         AND account.version=selected_actor_version)
     OR EXISTS (SELECT 1 FROM public.ai_business_v4_seals seal
       WHERE seal.run_id=parent.id)
  THEN RAISE EXCEPTION 'ai_v4_period_candidate_parent_invalid'; END IF;
  plan:=parent.plan_json::jsonb;
  IF parent.plan_json IS DISTINCT FROM public.ai_v4_replay_canonical(plan)
     OR plan->>'schemaVersion' IS DISTINCT FROM
       'business-evidence-v4-capacity-plan-v1'
     OR plan->>'planDigest' IS DISTINCT FROM
       encode(sha256(convert_to(public.ai_v4_replay_canonical(
         plan-'planDigest'),'UTF8')),'hex')
     OR plan->'runCapacitySupported' IS DISTINCT FROM 'true'::jsonb
     OR plan->'sourceAuthorityVerified' IS DISTINCT FROM 'false'::jsonb
     OR plan->'reportGenerationSupported' IS DISTINCT FROM 'false'::jsonb
     OR plan->'analysisRequest'->'requestedWindows' IS DISTINCT FROM
       '["current","previous","yearAgo"]'::jsonb
     OR jsonb_array_length(plan->'sourcePlans')<>4
  THEN RAISE EXCEPTION 'ai_v4_period_candidate_plan_invalid'; END IF;
  IF canonical_envelope IS DISTINCT FROM public.ai_v4_replay_canonical(
       canonical_envelope::jsonb)
     OR selected_envelope_digest IS DISTINCT FROM
       encode(sha256(convert_to(canonical_envelope,'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_v4_period_candidate_noncanonical'; END IF;
  body:=public.ai_screen_fields(canonical_envelope::json,ARRAY[
    'schemaVersion','basePlanSchema','basePlanDigest','runIdentityDigest',
    'analysisRequestDigest','comparisonRule','resolvedPeriods','dailySources',
    'financeContext','sourceAuthorityVerified','observedDailyCoverageVerified',
    'zeroDayCertificationVerified','financeDailyProrationAllowed',
    'agentCitationSupported','registeredRenderer','limitations',
    'periodPlanDigest']);
  IF body->>'schemaVersion' IS DISTINCT FROM
       'business-v4-jd-period-bound-plan-candidate-v1'
     OR body->>'basePlanSchema' IS DISTINCT FROM plan->>'schemaVersion'
     OR body->>'basePlanDigest' IS DISTINCT FROM plan->>'planDigest'
     OR body->>'runIdentityDigest' IS DISTINCT FROM parent.run_identity_digest
     OR body->>'analysisRequestDigest' IS DISTINCT FROM
       encode(sha256(convert_to(public.ai_v4_replay_canonical(
         plan->'analysisRequest'),'UTF8')),'hex')
     OR body->>'comparisonRule' IS DISTINCT FROM 'previous_equal_length_v1'
     OR body->>'periodPlanDigest' IS DISTINCT FROM
       encode(sha256(convert_to(public.ai_v4_replay_canonical(
         body-'periodPlanDigest'),'UTF8')),'hex')
     OR body->'sourceAuthorityVerified' IS DISTINCT FROM 'false'::jsonb
     OR body->'observedDailyCoverageVerified' IS DISTINCT FROM 'false'::jsonb
     OR body->'zeroDayCertificationVerified' IS DISTINCT FROM 'false'::jsonb
     OR body->'financeDailyProrationAllowed' IS DISTINCT FROM 'false'::jsonb
     OR body->'agentCitationSupported' IS DISTINCT FROM 'false'::jsonb
     OR body->'registeredRenderer' IS DISTINCT FROM 'false'::jsonb
     OR jsonb_typeof(body->'dailySources') IS DISTINCT FROM 'array'
     OR jsonb_array_length(body->'dailySources')<>3
     OR jsonb_typeof(body->'financeContext') IS DISTINCT FROM 'object'
  THEN RAISE EXCEPTION 'ai_v4_period_candidate_envelope_invalid'; END IF;
  SELECT actual.root_digest,actual.source_count INTO root,count_sources
    FROM public.ai_v4_seal_ticket_source_root(parent.id) actual;
  IF count_sources IS DISTINCT FROM 4 OR root !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_v4_period_candidate_source_root_invalid'; END IF;
  SELECT (s.query_json::jsonb->>'startDate')::date,
         (s.query_json::jsonb->>'endDate')::date
    INTO first_day,last_day FROM public.ai_business_v4_sources s
    WHERE s.run_id=parent.id AND s.domain='netshop'
      AND s.query_json::jsonb->>'window'='current';
  IF first_day IS NULL OR last_day IS NULL OR last_day<first_day
     OR last_day-first_day+1 NOT BETWEEN 1 AND 93
  THEN RAISE EXCEPTION 'ai_v4_period_candidate_current_period_invalid'; END IF;
  days:=last_day-first_day+1;
  requested_period:=jsonb_build_object(
    'startDate',to_char(first_day,'YYYY-MM-DD'),
    'endDate',to_char(last_day,'YYYY-MM-DD'),
    'endExclusive',to_char(last_day+1,'YYYY-MM-DD'),
    'days',days);
  expected_periods:=jsonb_build_object(
    'schemaVersion','business-comparison-periods-v2',
    'timezone','Asia/Shanghai','comparisonRule','previous_equal_length_v1',
    'requested',requested_period,'dataCutoffDate',NULL,
    'periodAdjustedToDataCutoff',false);
  expected_policy:=jsonb_build_object(
    'schemaVersion','business-v4-daily-coverage-obligation-v1',
    'observedRowsDoNotCertifyZeroDays',true,
    'zeroDayRequiresOwningProof',true,
    'missingOrUncertifiedDayIsNotZero',true,
    'numericComparisonRequiresBothWindowsComplete',true);
  FOR source IN SELECT s.* FROM public.ai_business_v4_sources s
      WHERE s.run_id=parent.id ORDER BY s.ordinal FOR UPDATE OF s LOOP
    entry:=plan->'sourcePlans'->(source.ordinal-1);
    query:=source.query_json::jsonb;
    IF NOT source.finished OR source.version<>source.page_count+1
       OR source.query_json IS DISTINCT FROM
         public.ai_v4_replay_canonical(query)
       OR entry->>'sourceKey' IS DISTINCT FROM source.source_key
       OR entry->'ordinal' IS DISTINCT FROM to_jsonb(source.ordinal)
       OR entry->>'domain' IS DISTINCT FROM source.domain
       OR entry->>'temporalRole' IS DISTINCT FROM source.temporal_role
       OR entry->'query' IS DISTINCT FROM query
       OR entry->>'queryDigest' IS DISTINCT FROM source.query_digest
       OR entry->>'sourceIdentityDigest' IS DISTINCT FROM source.source_identity_digest
       OR source.query_digest IS DISTINCT FROM
         encode(sha256(convert_to(source.query_json,'UTF8')),'hex')
    THEN RAISE EXCEPTION 'ai_v4_period_candidate_source_identity_invalid'; END IF;
    IF source.domain='finance' THEN
      finance_count:=finance_count+1;
      finance_item:=body->'financeContext';
      expected_finance:=jsonb_build_object(
        'sourceKey',source.source_key,'ordinal',source.ordinal,
        'domain','finance','temporalRole','monthly_context',
        'queryDigest',source.query_digest,
        'sourceIdentityDigest',source.source_identity_digest,
        'months',query->'months','scope',query->'scope',
        'analysisPeriod',query->'analysisPeriod',
        'dailyProrationAllowed',false,'shopIdentityMappingVerified',false);
      IF source.temporal_role<>'monthly_context'
         OR query->'analysisPeriod' IS DISTINCT FROM
           jsonb_build_object('startDate',to_char(first_day,'YYYY-MM-DD'),
             'endDate',to_char(last_day,'YYYY-MM-DD'))
         OR finance_item IS DISTINCT FROM expected_finance
      THEN RAISE EXCEPTION 'ai_v4_period_candidate_finance_invalid'; END IF;
    ELSE
      daily_count:=daily_count+1;
      IF source.domain<>'netshop' OR source.temporal_role<>'daily_fact'
         OR query->>'platform' IS DISTINCT FROM '京东'
         OR query->>'dataset' IS DISTINCT FROM 'promotion'
         OR query->>'startDate' IS DISTINCT FROM to_char(first_day,'YYYY-MM-DD')
         OR query->>'endDate' IS DISTINCT FROM to_char(last_day,'YYYY-MM-DD')
         OR query->>'shop' IS DISTINCT FROM
           (SELECT current_source.query_json::jsonb->>'shop'
              FROM public.ai_business_v4_sources current_source
              WHERE current_source.run_id=parent.id
                AND current_source.domain='netshop'
                AND current_source.query_json::jsonb->>'window'='current')
         OR query->>'window' NOT IN ('current','previous','yearAgo')
      THEN RAISE EXCEPTION 'ai_v4_period_candidate_daily_source_invalid'; END IF;
      IF query->>'window'='current' THEN
        window_first:=first_day; window_last:=last_day;
      ELSIF query->>'window'='previous' THEN
        window_first:=first_day-days; window_last:=first_day-1;
      ELSE
        first_year_month_end:=(make_date(extract(year from first_day)::integer-1,
          extract(month from first_day)::integer,1)+interval '1 month'
          -interval '1 day')::date;
        last_year_month_end:=(make_date(extract(year from last_day)::integer-1,
          extract(month from last_day)::integer,1)+interval '1 month'
          -interval '1 day')::date;
        window_first:=make_date(extract(year from first_day)::integer-1,
          extract(month from first_day)::integer,
          least(extract(day from first_day)::integer,
            extract(day from first_year_month_end)::integer));
        window_last:=make_date(extract(year from last_day)::integer-1,
          extract(month from last_day)::integer,
          least(extract(day from last_day)::integer,
            extract(day from last_year_month_end)::integer));
      END IF;
      expected_period:=jsonb_build_object(
        'startDate',to_char(window_first,'YYYY-MM-DD'),
        'endDate',to_char(window_last,'YYYY-MM-DD'),
        'endExclusive',to_char(window_last+1,'YYYY-MM-DD'),
        'days',window_last-window_first+1);
      expected_periods:=expected_periods ||
        jsonb_build_object(query->>'window',expected_period);
      SELECT jsonb_agg(to_char(day,'YYYY-MM-DD') ORDER BY day)
        INTO expected_days FROM generate_series(window_first,window_last,
          interval '1 day') AS generated(day);
      item:=body->'dailySources'->(daily_count-1);
      expected_item:=jsonb_build_object(
        'sourceKey',source.source_key,'ordinal',source.ordinal,
        'domain',source.domain,'temporalRole',source.temporal_role,
        'platform',query->>'platform','shop',query->>'shop',
        'dataset',query->>'dataset',
        'queryDigest',source.query_digest,
        'sourceIdentityDigest',source.source_identity_digest,
        'originalQueryStartDate',query->>'startDate',
        'originalQueryEndDate',query->>'endDate',
        'window',query->>'window','resolvedPeriod',expected_period,
        'expectedDayCount',window_last-window_first+1,
        'expectedDayDigest',encode(sha256(convert_to(
          public.ai_v4_replay_canonical(jsonb_build_object(
            'schemaVersion','business-v4-expected-source-days-v1',
            'sourceKey',source.source_key,'window',query->>'window',
            'dates',expected_days)),'UTF8')),'hex'),
        'coveragePolicy',expected_policy,
        'observedDailyCoverageVerified',false,
        'zeroDayCertificationVerified',false);
      IF item IS DISTINCT FROM expected_item
      THEN RAISE EXCEPTION 'ai_v4_period_candidate_daily_plan_invalid'; END IF;
    END IF;
  END LOOP;
  IF daily_count<>3 OR finance_count<>1
     OR body->'resolvedPeriods' IS DISTINCT FROM expected_periods
  THEN RAISE EXCEPTION 'ai_v4_period_candidate_windows_invalid'; END IF;
  INSERT INTO public.ai_business_v4_period_plan_candidates
    (run_id,attempt_id,owner_email,actor_version,plan_digest,
     directory_digest,source_root,envelope_json,envelope_digest,created_at)
    VALUES (parent.id,attempt.id,selected_actor,selected_actor_version,
      parent.plan_digest,attempt.directory_digest,root,canonical_envelope,
      selected_envelope_digest,clock_timestamp())
    ON CONFLICT (run_id) DO NOTHING;
  GET DIAGNOSTICS inserted_count=ROW_COUNT;
  IF inserted_count=1 THEN
    UPDATE public.ai_data_revisions SET revision=revision+1,
      updated_at=clock_timestamp() WHERE domain='ai-assistant';
    IF NOT FOUND THEN RAISE EXCEPTION 'ai_v4_period_candidate_revision_missing'; END IF;
  END IF;
  SELECT * INTO saved FROM public.ai_business_v4_period_plan_candidates p
    WHERE p.run_id=parent.id;
  IF saved.run_id IS NULL OR saved.attempt_id<>attempt.id
     OR saved.owner_email<>selected_actor
     OR saved.actor_version<>selected_actor_version
     OR saved.plan_digest<>parent.plan_digest
     OR saved.directory_digest<>attempt.directory_digest
     OR saved.source_root<>root
     OR saved.envelope_json<>canonical_envelope
     OR saved.envelope_digest<>selected_envelope_digest
  THEN RAISE EXCEPTION 'ai_v4_period_candidate_existing_conflict'; END IF;
  envelope_digest:=saved.envelope_digest::text;
  source_root:=saved.source_root::text; created_at:=saved.created_at;
  RETURN NEXT;
END $$"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        predecessors = (
            ("public.ai_business_v4_run_guard()",
             "0038_business_v4_seal_writer_gate", "RUN_GUARD"),
            ("public.ai_business_v4_source_guard()",
             "0035_business_v4_ledger", "SOURCE_GUARD"),
            ("public.ai_v4_replay_canonical(jsonb)",
             "0047_business_v4_sealer_replay_progress", "CANONICAL_SQL"),
            ("public.ai_v4_seal_ticket_source_root(text)",
             "0041_business_v4_seal_ticket", "SOURCE_ROOT"),
            ("public.ai_v4_lock_source_revisions_for_admission()",
             "0038_business_v4_seal_writer_gate", "LOCK_PROBE"),
        )
        for signature, migration, name in predecessors:
            cursor.execute("SELECT p.prosrc FROM pg_catalog.pg_proc p "
                "WHERE p.oid=to_regprocedure(%s)", [signature])
            found = cursor.fetchone()
            frozen = getattr(import_module("ai_assistant.migrations." + migration),
                name).split("$$")[1]
            if found is None or found[0] != frozen:
                raise RuntimeError("0055 requires frozen v4 source and plan guards")
        cursor.execute("""CREATE TABLE public.ai_business_v4_period_plan_candidates (
          run_id varchar(160) PRIMARY KEY REFERENCES public.ai_business_v4_runs(id)
            ON DELETE RESTRICT,
          attempt_id varchar(160) NOT NULL REFERENCES
            public.ai_business_v4_validation_attempts(id) ON DELETE RESTRICT,
          owner_email varchar(320) NOT NULL,
          actor_version bigint NOT NULL CHECK (actor_version>=1),
          plan_digest varchar(64) NOT NULL,
          directory_digest varchar(64) NOT NULL,
          source_root varchar(64) NOT NULL,
          envelope_json text NOT NULL CHECK (octet_length(envelope_json)<=65536),
          envelope_digest varchar(64) NOT NULL,
          created_at timestamptz NOT NULL
        )""")
        cursor.execute("REVOKE ALL ON " + TABLE + " FROM PUBLIC")
        for role in ("teruisi_ai_writer", "teruisi_ai_reader",
                     "teruisi_ai_seal_writer"):
            cursor.execute("SELECT to_regrole(%s)", [role])
            if cursor.fetchone()[0] is not None:
                cursor.execute("REVOKE ALL ON " + TABLE + " FROM " + role)
        for definition in (GUARD, NO_TRUNCATE, RECORD):
            cursor.execute(definition)
        for signature in ("public.ai_v4_period_plan_candidate_guard()",
                          "public.ai_v4_period_plan_no_truncate()", WRITE):
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
        for role in ("teruisi_ai_reader", "teruisi_ai_seal_writer"):
            cursor.execute("SELECT to_regrole(%s)", [role])
            if cursor.fetchone()[0] is not None:
                cursor.execute("REVOKE ALL ON FUNCTION " + WRITE + " FROM " + role)
        cursor.execute("SELECT to_regrole('teruisi_ai_writer')")
        if cursor.fetchone()[0] is not None:
            cursor.execute("GRANT EXECUTE ON FUNCTION " + WRITE +
                " TO teruisi_ai_writer")
        cursor.execute("CREATE TRIGGER ai_v4_period_candidate_state "
            "BEFORE INSERT OR UPDATE OR DELETE ON " + TABLE +
            " FOR EACH ROW EXECUTE FUNCTION "
            "public.ai_v4_period_plan_candidate_guard()")
        cursor.execute("CREATE TRIGGER ai_v4_period_candidate_no_truncate "
            "BEFORE TRUNCATE ON " + TABLE +
            " FOR EACH STATEMENT EXECUTE FUNCTION "
            "public.ai_v4_period_plan_no_truncate()")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM " + TABLE + ")")
        if cursor.fetchone()[0]:
            raise RuntimeError("0055 cannot discard a period-plan candidate")
        cursor.execute("DROP FUNCTION " + WRITE)
        for name in ("ai_v4_period_candidate_no_truncate",
                     "ai_v4_period_candidate_state"):
            cursor.execute("DROP TRIGGER " + name + " ON " + TABLE)
        cursor.execute("DROP FUNCTION public.ai_v4_period_plan_no_truncate()")
        cursor.execute("DROP FUNCTION public.ai_v4_period_plan_candidate_guard()")
        cursor.execute("DROP TABLE " + TABLE)


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0054_business_promotion_budget_file_staging")]
    operations = [
        migrations.SeparateDatabaseAndState(database_operations=[],
            state_operations=[migrations.CreateModel(
                name="AiBusinessV4PeriodPlanCandidate", fields=[
                    ("run", models.OneToOneField(primary_key=True,
                        serialize=False, on_delete=django.db.models.deletion.PROTECT,
                        to="ai_assistant.aibusinessv4run")),
                    ("attempt", models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="ai_assistant.aibusinessv4validationattempt")),
                    ("owner_email", models.CharField(max_length=320)),
                    ("actor_version", models.PositiveBigIntegerField()),
                    ("plan_digest", models.CharField(max_length=64)),
                    ("directory_digest", models.CharField(max_length=64)),
                    ("source_root", models.CharField(max_length=64)),
                    ("envelope_json", models.TextField()),
                    ("envelope_digest", models.CharField(max_length=64)),
                    ("created_at", models.DateTimeField(
                        default=django.utils.timezone.now)),
                ], options={"db_table": "ai_business_v4_period_plan_candidates"})]),
        migrations.RunPython(install, uninstall),
    ]

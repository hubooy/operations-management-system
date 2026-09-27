"""Inert v4 resumable validation witnesses; parent sealing remains forbidden."""
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


ATTEMPT_GUARD = """CREATE FUNCTION ai_business_v4_validation_attempt_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_v4_runs%ROWTYPE; count_all bigint; total bigint;
  pages bigint; rows bigint; bytes bigint; account_version bigint;
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'ai_business_v4_attempt_immutable'; END IF;
  SELECT * INTO parent FROM public.ai_business_v4_runs WHERE id=NEW.run_id FOR UPDATE;
  SELECT version INTO account_version FROM public.access_control_users
    WHERE email=NEW.actor_email AND role='admin' AND status='active' AND scope IS NULL;
  SELECT count(*) INTO count_all FROM public.ai_business_v4_validation_attempts WHERE run_id=NEW.run_id;
  SELECT count(*),coalesce(sum(page_count),0),coalesce(sum(row_count),0),
         coalesce(sum(stored_bytes),0) INTO total,pages,rows,bytes
    FROM public.ai_business_v4_sources WHERE run_id=NEW.run_id;
  IF parent.id IS NULL OR parent.status<>'collecting' OR parent.collection_status<>'manual'
     OR NEW.run_version<>parent.version OR NEW.plan_digest<>parent.plan_digest
     OR NEW.actor_email<>parent.owner_email OR NEW.actor_version IS DISTINCT FROM account_version
     OR NEW.actor_version<1 OR NEW.key_id !~ '^[a-f0-9]{16}$'
     OR NEW.directory_digest !~ '^[a-f0-9]{64}$'
     OR count_all>=4 OR total NOT BETWEEN 2 AND 4
     OR pages<>parent.page_count OR rows<>parent.row_count OR bytes<>parent.stored_bytes
     OR EXISTS (SELECT 1 FROM public.ai_business_v4_sources s WHERE s.run_id=NEW.run_id
       AND (NOT s.finished OR s.version<>s.page_count+1 OR s.page_count<1
         OR s.domain NOT IN ('netshop','finance')
         OR (s.domain='netshop' AND (s.query_json::jsonb->>'platform'<>'京东'
             OR s.query_json::jsonb->>'dataset'<>'promotion'))))
     OR (SELECT count(*) FROM public.ai_business_v4_sources s
         WHERE s.run_id=NEW.run_id AND s.domain='finance')<>1
     OR (SELECT count(*) FROM public.ai_business_v4_sources s
         WHERE s.run_id=NEW.run_id AND s.domain='netshop') NOT BETWEEN 1 AND 3
  THEN RAISE EXCEPTION 'ai_business_v4_attempt_initial_invalid'; END IF;
  RETURN NEW;
END $$"""


SEGMENT_GUARD = """CREATE FUNCTION ai_business_v4_validation_segment_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE attempt public.ai_business_v4_validation_attempts%ROWTYPE;
  parent public.ai_business_v4_runs%ROWTYPE; source public.ai_business_v4_sources%ROWTYPE;
  prior public.ai_business_v4_validation_segments%ROWTYPE; account_version bigint;
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'ai_business_v4_segment_immutable'; END IF;
  SELECT * INTO attempt FROM public.ai_business_v4_validation_attempts WHERE id=NEW.attempt_id;
  SELECT * INTO parent FROM public.ai_business_v4_runs WHERE id=NEW.run_id FOR UPDATE;
  SELECT * INTO source FROM public.ai_business_v4_sources WHERE id=NEW.source_id FOR UPDATE;
  SELECT version INTO account_version FROM public.access_control_users
    WHERE email=attempt.actor_email AND role='admin' AND status='active' AND scope IS NULL;
  SELECT * INTO prior FROM public.ai_business_v4_validation_segments
    WHERE attempt_id=NEW.attempt_id AND source_id=NEW.source_id
    ORDER BY segment_index DESC LIMIT 1;
  IF attempt.id IS NULL OR parent.id IS NULL OR source.id IS NULL
     OR attempt.run_id<>parent.id OR source.run_id<>parent.id
     OR parent.status<>'collecting' OR parent.collection_status<>'manual'
     OR NOT source.finished OR attempt.run_version<>parent.version
     OR attempt.plan_digest<>parent.plan_digest OR attempt.actor_email<>parent.owner_email
     OR attempt.actor_version IS DISTINCT FROM account_version
     OR NEW.source_version<>source.version OR NEW.source_ref<>source.source_ref
     OR NEW.source_revision<>source.source_revision
     OR NEW.segment_index NOT BETWEEN 1 AND 1024
     OR NEW.start_sequence<>(NEW.segment_index-1)*16+1
     OR NEW.end_sequence<>least(NEW.segment_index*16,source.page_count)
     OR NEW.start_sequence>NEW.end_sequence
     OR octet_length(NEW.progress_json)>32768
     OR NEW.progress_digest IS DISTINCT FROM
        encode(sha256(convert_to(NEW.progress_json,'UTF8')),'hex')
     OR NEW.proof_digest !~ '^[a-f0-9]{64}$' OR NEW.proof_mac !~ '^[a-f0-9]{64}$'
     OR (NEW.segment_index=1 AND (prior.id IS NOT NULL OR
         NEW.previous_segment_digest<>'0000000000000000000000000000000000000000000000000000000000000000'))
     OR (NEW.segment_index>1 AND (prior.id IS NULL
         OR prior.segment_index<>NEW.segment_index-1
         OR NEW.previous_segment_digest<>prior.proof_digest))
     OR EXISTS (SELECT 1 FROM public.ai_business_v4_validation_attempts newer
       WHERE newer.run_id=parent.id AND (newer.created_at,newer.id)>
         (attempt.created_at,attempt.id))
  THEN RAISE EXCEPTION 'ai_business_v4_segment_insert_invalid'; END IF;
  RETURN NEW;
END $$"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql": return
    with schema_editor.connection.cursor() as cursor:
        for table in ("ai_business_v4_validation_attempts",
                      "ai_business_v4_validation_segments"):
            cursor.execute(f"CREATE TRIGGER ai_write_fence BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION ai_runtime_write_fence()")
            cursor.execute(f"CREATE TRIGGER ai_immutable_v4 BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION ai_immutable_record_guard()")
            cursor.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        cursor.execute(ATTEMPT_GUARD)
        cursor.execute(SEGMENT_GUARD)
        cursor.execute("CREATE TRIGGER ai_v4_state BEFORE INSERT OR UPDATE OR DELETE ON ai_business_v4_validation_attempts FOR EACH ROW EXECUTE FUNCTION ai_business_v4_validation_attempt_guard()")
        cursor.execute("CREATE TRIGGER ai_v4_state BEFORE INSERT OR UPDATE OR DELETE ON ai_business_v4_validation_segments FOR EACH ROW EXECUTE FUNCTION ai_business_v4_validation_segment_guard()")
        cursor.execute("DO $$ BEGIN IF to_regrole('teruisi_ai_writer') IS NOT NULL THEN GRANT SELECT,INSERT ON ai_business_v4_validation_attempts,ai_business_v4_validation_segments TO teruisi_ai_writer; END IF; IF to_regrole('teruisi_ai_reader') IS NOT NULL THEN REVOKE ALL ON ai_business_v4_validation_attempts,ai_business_v4_validation_segments FROM teruisi_ai_reader; END IF; END $$")


def uninstall(apps, schema_editor):
    for name in ("AiBusinessV4ValidationSegment", "AiBusinessV4ValidationAttempt"):
        if apps.get_model("ai_assistant", name).objects.exists():
            raise RuntimeError("存在v4候选分段证明，不能逆迁移并丢弃")
    if schema_editor.connection.vendor != "postgresql": return
    with schema_editor.connection.cursor() as cursor:
        for table in ("ai_business_v4_validation_segments",
                      "ai_business_v4_validation_attempts"):
            for trigger in ("ai_v4_state", "ai_immutable_v4", "ai_write_fence"):
                cursor.execute(f"DROP TRIGGER {trigger} ON {table}")
        cursor.execute("DROP FUNCTION ai_business_v4_validation_segment_guard()")
        cursor.execute("DROP FUNCTION ai_business_v4_validation_attempt_guard()")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0035_business_v4_ledger")]
    operations = [
        migrations.CreateModel(name="AiBusinessV4ValidationAttempt", fields=[
            ("id", models.CharField(primary_key=True, serialize=False, max_length=160)),
            ("run", models.ForeignKey(to="ai_assistant.aibusinessv4run",
                on_delete=django.db.models.deletion.PROTECT)),
            ("run_version", models.PositiveBigIntegerField()),
            ("plan_digest", models.CharField(max_length=64)),
            ("directory_digest", models.CharField(max_length=64)),
            ("actor_email", models.CharField(max_length=320)),
            ("actor_version", models.PositiveBigIntegerField()),
            ("key_id", models.CharField(max_length=16)),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
        ], options={"db_table": "ai_business_v4_validation_attempts",
            "indexes": [models.Index(fields=["run", "-created_at"],
                name="ai_v4_attempt_run_idx")]}),
        migrations.CreateModel(name="AiBusinessV4ValidationSegment", fields=[
            ("id", models.CharField(primary_key=True, serialize=False, max_length=160)),
            ("attempt", models.ForeignKey(to="ai_assistant.aibusinessv4validationattempt",
                on_delete=django.db.models.deletion.PROTECT)),
            ("run", models.ForeignKey(to="ai_assistant.aibusinessv4run",
                on_delete=django.db.models.deletion.PROTECT)),
            ("source", models.ForeignKey(to="ai_assistant.aibusinessv4source",
                on_delete=django.db.models.deletion.PROTECT)),
            ("segment_index", models.PositiveIntegerField()),
            ("start_sequence", models.PositiveIntegerField()),
            ("end_sequence", models.PositiveIntegerField()),
            ("source_version", models.PositiveBigIntegerField()),
            ("source_ref", models.CharField(max_length=64)),
            ("source_revision", models.CharField(max_length=128)),
            ("previous_segment_digest", models.CharField(max_length=64)),
            ("progress_json", models.TextField()),
            ("progress_digest", models.CharField(max_length=64)),
            ("proof_digest", models.CharField(max_length=64)),
            ("proof_mac", models.CharField(max_length=64)),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
        ], options={"db_table": "ai_business_v4_validation_segments",
            "constraints": [models.UniqueConstraint(fields=["attempt", "source", "segment_index"],
                name="ai_v4_segment_seq_uq")],
            "indexes": [models.Index(fields=["attempt", "source", "-segment_index"],
                name="ai_v4_segment_next_idx")]}),
        migrations.RunPython(install, uninstall),
    ]

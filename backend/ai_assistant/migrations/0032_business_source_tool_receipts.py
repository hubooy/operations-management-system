"""Immutable v3 chunk to successful central-tool audit binding.

Old physical chunks remain unchanged and receive no synthetic receipt. A later
seal must reject a v3 source unless every one of its chunks has a valid receipt.
"""
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


GUARD = """CREATE FUNCTION ai_business_source_tool_receipt_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_evidence_runs%ROWTYPE;
  source public.ai_business_evidence_sources%ROWTYPE;
  chunk public.ai_business_evidence_chunks%ROWTYPE;
  audit public.ai_tool_audit_logs%ROWTYPE;
  expected_tool text; page jsonb;
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'ai_business_tool_receipt_immutable'; END IF;
  SELECT * INTO parent FROM public.ai_business_evidence_runs WHERE id=NEW.run_id FOR UPDATE;
  SELECT * INTO source FROM public.ai_business_evidence_sources WHERE id=NEW.source_id FOR UPDATE;
  SELECT * INTO chunk FROM public.ai_business_evidence_chunks WHERE id=NEW.chunk_id;
  SELECT * INTO audit FROM public.ai_tool_audit_logs WHERE id=NEW.audit_id;
  IF NOT FOUND OR parent.id IS NULL OR source.id IS NULL OR chunk.id IS NULL
     OR public.ai_business_v3_header(parent.plan_json) IS NULL
     OR parent.status<>'collecting' OR parent.collection_status<>'manual'
     OR parent.scope_json<>'null' OR source.run_id<>parent.id
     OR chunk.run_id<>parent.id OR chunk.source_key<>source.source_key
     OR NEW.sequence<>chunk.sequence OR NEW.sequence<>source.page_count+1
     OR NEW.sequence NOT BETWEEN 1 AND 1999
     OR NEW.actor_email<>parent.owner_email OR NEW.actor_email<>audit.actor_email
     OR NEW.request_id<>audit.request_id OR NEW.invocation_id<>audit.invocation_id
     OR NEW.tool_name<>audit.tool_name OR NEW.surface<>'business_collection'
     OR audit.surface<>'business_collection' OR audit.actor_role<>'admin'
     OR audit.status<>'succeeded' OR audit.error_code IS NOT NULL
     OR NEW.response_digest IS DISTINCT FROM audit.response_digest
     OR NEW.response_digest<>chunk.payload_digest
     OR NEW.response_digest !~ '^[0-9a-f]{64}$'
     OR NEW.payload_bytes<>octet_length(chunk.payload_json)
     OR NEW.payload_bytes NOT BETWEEN 1 AND 131072
     OR chunk.payload_digest<>encode(sha256(convert_to(chunk.payload_json,'UTF8')),'hex')
     OR NEW.created_at<audit.created_at
  THEN RAISE EXCEPTION 'ai_business_tool_receipt_binding_invalid'; END IF;
  IF source.domain='finance' THEN
    expected_tool:='get_business_finance_source_page';
    IF NEW.payload_bytes>38000 THEN RAISE EXCEPTION 'ai_business_finance_receipt_bytes'; END IF;
  ELSIF source.domain IN ('sales','netshop','market') THEN
    IF NEW.sequence=1 THEN expected_tool:='get_business_source_page';
    ELSIF source.domain='sales' THEN expected_tool:='get_business_sales_continuation_page';
    ELSIF source.domain='netshop' THEN expected_tool:='get_business_netshop_continuation_page';
    ELSE expected_tool:='get_business_market_continuation_page'; END IF;
  ELSE RAISE EXCEPTION 'ai_business_tool_receipt_domain_invalid'; END IF;
  page:=chunk.payload_json::jsonb;
  IF NEW.tool_name<>expected_tool
     OR NEW.source_ref IS DISTINCT FROM page->>'sourceRef'
     OR NEW.source_revision IS DISTINCT FROM page->>'sourceRevision'
     OR NEW.source_ref !~ '^[0-9a-f]{64}$'
     OR length(NEW.source_revision) NOT BETWEEN 1 AND 128
     OR NEW.request_id !~ '^[A-Za-z0-9_-]{1,128}$'
     OR NEW.invocation_id !~ '^[A-Za-z0-9._:-]{1,160}$'
  THEN RAISE EXCEPTION 'ai_business_tool_receipt_page_invalid'; END IF;
  RETURN NEW;
END $$"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("CREATE TRIGGER ai_write_fence BEFORE INSERT OR UPDATE OR DELETE ON ai_business_source_tool_receipts FOR EACH ROW EXECUTE FUNCTION ai_runtime_write_fence()")
        cursor.execute("CREATE TRIGGER ai_immutable_receipt BEFORE UPDATE OR DELETE ON ai_business_source_tool_receipts FOR EACH ROW EXECUTE FUNCTION ai_immutable_record_guard()")
        cursor.execute(GUARD)
        cursor.execute("CREATE TRIGGER ai_business_source_tool_receipt BEFORE INSERT ON ai_business_source_tool_receipts FOR EACH ROW EXECUTE FUNCTION ai_business_source_tool_receipt_guard()")


def uninstall(apps, schema_editor):
    if apps.get_model("ai_assistant", "AiBusinessSourceToolReceipt").objects.exists():
        raise RuntimeError("存在v3签名工具收据，不能逆迁移并丢弃来源证明")
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for trigger in ("ai_business_source_tool_receipt", "ai_immutable_receipt", "ai_write_fence"):
            cursor.execute(f"DROP TRIGGER {trigger} ON ai_business_source_tool_receipts")
        cursor.execute("DROP FUNCTION ai_business_source_tool_receipt_guard()")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0031_business_daily_v3_source_pages")]
    operations = [
        migrations.CreateModel(name="AiBusinessSourceToolReceipt", fields=[
            ("chunk", models.OneToOneField(primary_key=True, serialize=False, db_column="chunk_id",
                on_delete=django.db.models.deletion.PROTECT, to="ai_assistant.aibusinessevidencechunk")),
            ("audit", models.OneToOneField(db_column="audit_id", on_delete=django.db.models.deletion.PROTECT,
                to="ai_assistant.aitoolauditlogs")),
            ("run", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                to="ai_assistant.aibusinessevidencerun")),
            ("source", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                to="ai_assistant.aibusinessevidencesource")),
            ("sequence", models.PositiveIntegerField()),
            ("request_id", models.CharField(max_length=128)),
            ("invocation_id", models.CharField(max_length=160)),
            ("actor_email", models.CharField(max_length=320)),
            ("tool_name", models.CharField(max_length=100)),
            ("surface", models.CharField(max_length=40, default="business_collection")),
            ("response_digest", models.CharField(max_length=64)),
            ("payload_bytes", models.PositiveIntegerField()),
            ("source_ref", models.CharField(max_length=64)),
            ("source_revision", models.CharField(max_length=128)),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
        ], options={"db_table": "ai_business_source_tool_receipts",
            "constraints": [models.UniqueConstraint(fields=("run", "source", "sequence"),
                name="ai_business_source_tool_seq_uq")],
            "indexes": [models.Index(fields=("run", "source", "sequence"),
                name="ai_business_tool_receipt_idx")]}),
        migrations.RunPython(install, uninstall),
    ]

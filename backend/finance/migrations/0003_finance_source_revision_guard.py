"""Require a finance-source revision change in the *same* fact transaction.

The protected marker records the first revision visible to a transaction.
Every source-table write takes the revision row lock; a deferred trigger rejects
commit unless that same transaction advanced revision and changed its digest.
The application finance writer receives no direct marker permissions.
"""
from django.db import migrations


INSTALL = """
INSERT INTO public.finance_data_revisions(domain,revision,source_digest,updated_at)
  VALUES ('finance',0,repeat('0',64),now()) ON CONFLICT (domain) DO NOTHING;
-- STEP --
CREATE TABLE public.finance_source_revision_markers (
    transaction_id bigint PRIMARY KEY,
    baseline_revision bigint NOT NULL,
    baseline_digest varchar(64) NOT NULL
);
-- STEP --
REVOKE ALL ON public.finance_source_revision_markers FROM PUBLIC;
-- STEP --

CREATE FUNCTION public.finance_source_mark_revision_required() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE existing_id bigint; prior_revision bigint; prior_digest text;
  current_transaction bigint;
BEGIN
  current_transaction := txid_current();
  SELECT transaction_id INTO existing_id
    FROM public.finance_source_revision_markers
    WHERE transaction_id=current_transaction;
  IF FOUND THEN RETURN NULL; END IF;

  SELECT revision,source_digest INTO prior_revision,prior_digest
    FROM public.finance_data_revisions WHERE domain='finance' FOR UPDATE;
  IF NOT FOUND THEN
    INSERT INTO public.finance_data_revisions(domain,revision,source_digest,updated_at)
      VALUES ('finance',0,repeat('0',64),now()) ON CONFLICT (domain) DO NOTHING;
    SELECT revision,source_digest INTO prior_revision,prior_digest
      FROM public.finance_data_revisions WHERE domain='finance' FOR UPDATE;
  END IF;
  IF NOT FOUND OR prior_revision IS NULL OR prior_revision<0
     OR prior_revision>9007199254740991
     OR prior_digest IS NULL OR prior_digest !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'finance_source_revision_baseline_invalid'; END IF;
  INSERT INTO public.finance_source_revision_markers
    (transaction_id,baseline_revision,baseline_digest)
    VALUES (current_transaction,prior_revision,prior_digest);
  RETURN NULL;
END $$;
-- STEP --

CREATE FUNCTION public.finance_source_revision_required_at_commit() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE current_revision bigint; current_digest text;
BEGIN
  IF NEW.transaction_id IS DISTINCT FROM txid_current()
  THEN RAISE EXCEPTION 'finance_source_revision_marker_not_owned'; END IF;
  SELECT revision,source_digest INTO current_revision,current_digest
    FROM public.finance_data_revisions WHERE domain='finance' FOR UPDATE;
  IF NOT FOUND OR current_revision IS NULL
     OR current_revision<=NEW.baseline_revision
     OR current_revision>9007199254740991
     OR current_digest IS NULL OR current_digest !~ '^[0-9a-f]{64}$'
     OR current_digest=NEW.baseline_digest
  THEN RAISE EXCEPTION 'finance_source_write_without_revision'; END IF;
  DELETE FROM public.finance_source_revision_markers
    WHERE transaction_id=NEW.transaction_id;
  RETURN NULL;
END $$;
-- STEP --

REVOKE ALL ON FUNCTION public.finance_source_mark_revision_required() FROM PUBLIC;
-- STEP --
REVOKE ALL ON FUNCTION public.finance_source_revision_required_at_commit() FROM PUBLIC;
-- STEP --

CREATE CONSTRAINT TRIGGER finance_source_revision_required
  AFTER INSERT ON public.finance_source_revision_markers
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
  EXECUTE FUNCTION public.finance_source_revision_required_at_commit();
-- STEP --

CREATE TRIGGER finance_line_revision_required
  BEFORE INSERT OR UPDATE OR DELETE ON public.finance_lines
  FOR EACH STATEMENT EXECUTE FUNCTION public.finance_source_mark_revision_required();
-- STEP --
CREATE TRIGGER finance_month_revision_required
  BEFORE INSERT OR UPDATE OR DELETE ON public.finance_months
  FOR EACH STATEMENT EXECUTE FUNCTION public.finance_source_mark_revision_required();
-- STEP --
CREATE TRIGGER finance_batch_revision_required
  BEFORE INSERT OR UPDATE OR DELETE ON public.finance_import_batches
  FOR EACH STATEMENT EXECUTE FUNCTION public.finance_source_mark_revision_required();
"""


UNINSTALL = """
DROP TRIGGER IF EXISTS finance_line_revision_required ON public.finance_lines;
DROP TRIGGER IF EXISTS finance_month_revision_required ON public.finance_months;
DROP TRIGGER IF EXISTS finance_batch_revision_required ON public.finance_import_batches;
DROP TRIGGER IF EXISTS finance_source_revision_required ON public.finance_source_revision_markers;
DROP FUNCTION IF EXISTS public.finance_source_mark_revision_required();
DROP FUNCTION IF EXISTS public.finance_source_revision_required_at_commit();
DROP TABLE IF EXISTS public.finance_source_revision_markers;
"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cursor:
            for statement in INSTALL.split("\n-- STEP --\n"):
                cursor.execute(statement)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT EXISTS(SELECT 1 FROM public.finance_lines) OR "
                "EXISTS(SELECT 1 FROM public.finance_months) OR "
                "EXISTS(SELECT 1 FROM public.finance_import_batches) OR "
                "NOT EXISTS(SELECT 1 FROM public.finance_write_authority "
                "WHERE id=1 AND status='d1') OR "
                "NOT EXISTS(SELECT 1 FROM public.finance_data_revisions "
                "WHERE domain='finance' AND revision=0 AND source_digest=repeat('0',64)) OR "
                "EXISTS(SELECT 1 FROM pg_catalog.pg_stat_activity "
                "WHERE datname=current_database() AND usename='teruisi_finance_writer' "
                "AND pid<>pg_backend_pid())")
            if cursor.fetchone()[0]:
                raise RuntimeError("存在财报事实、修订或活动writer，不能逆迁移并失去写入门禁")
            for statement in UNINSTALL.split(";"):
                if statement.strip():
                    cursor.execute(statement)


class Migration(migrations.Migration):
    dependencies = [("finance", "0002_finance_target_gross_margin")]
    operations = [migrations.RunPython(install, uninstall)]

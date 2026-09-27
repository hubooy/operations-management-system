"""Bind netshop owning facts to a monotonic revision in one transaction.

The signed business-analysis reader depends on rows, completed import batches
and the global revision. A protected marker records the pre-write revision;
the deferred check rejects fact commits without a new revision/digest. The
revision row itself cannot be deleted or returned to an earlier identity.
"""
from django.db import migrations


INSTALL = """
INSERT INTO public.netshop_data_revisions(domain,revision,source_digest,updated_at)
  VALUES ('netshop',0,repeat('0',64),now()) ON CONFLICT (domain) DO NOTHING;
-- STEP --
CREATE TABLE public.netshop_source_revision_markers (
    transaction_id bigint PRIMARY KEY,
    baseline_revision bigint NOT NULL,
    baseline_digest varchar(64) NOT NULL
);
-- STEP --
REVOKE ALL ON public.netshop_source_revision_markers FROM PUBLIC;
-- STEP --

CREATE FUNCTION public.netshop_source_revision_monotonic() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  IF TG_OP='DELETE' THEN
    IF OLD.domain='netshop' THEN
      RAISE EXCEPTION 'netshop_source_revision_delete_denied';
    END IF;
    RETURN OLD;
  END IF;
  IF TG_OP='INSERT' THEN
    IF NEW.domain='netshop' AND (
         NEW.revision IS NULL OR NEW.revision<0
         OR NEW.revision>9007199254740991
         OR NEW.source_digest IS NULL OR NEW.source_digest !~ '^[0-9a-f]{64}$')
    THEN RAISE EXCEPTION 'netshop_source_revision_initial_invalid'; END IF;
    RETURN NEW;
  END IF;
  IF OLD.domain='netshop' OR NEW.domain='netshop' THEN
    IF NEW.domain IS DISTINCT FROM OLD.domain
       OR NEW.revision IS NULL OR NEW.revision<OLD.revision
       OR NEW.revision>9007199254740991
       OR NEW.source_digest IS NULL OR NEW.source_digest !~ '^[0-9a-f]{64}$'
       OR (NEW.revision=OLD.revision
           AND NEW.source_digest IS DISTINCT FROM OLD.source_digest)
    THEN RAISE EXCEPTION 'netshop_source_revision_rollback_or_aba'; END IF;
  END IF;
  RETURN NEW;
END $$;
-- STEP --

CREATE FUNCTION public.netshop_source_mark_revision_required() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE existing_id bigint; prior_revision bigint; prior_digest text;
  current_transaction bigint;
BEGIN
  current_transaction := txid_current();
  SELECT transaction_id INTO existing_id
    FROM public.netshop_source_revision_markers
    WHERE transaction_id=current_transaction;
  IF FOUND THEN RETURN NULL; END IF;

  SELECT revision,source_digest INTO prior_revision,prior_digest
    FROM public.netshop_data_revisions WHERE domain='netshop' FOR UPDATE;
  IF NOT FOUND THEN
    INSERT INTO public.netshop_data_revisions(domain,revision,source_digest,updated_at)
      VALUES ('netshop',0,repeat('0',64),now()) ON CONFLICT (domain) DO NOTHING;
    SELECT revision,source_digest INTO prior_revision,prior_digest
      FROM public.netshop_data_revisions WHERE domain='netshop' FOR UPDATE;
  END IF;
  IF NOT FOUND OR prior_revision IS NULL OR prior_revision<0
     OR prior_revision>9007199254740991
     OR prior_digest IS NULL OR prior_digest !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'netshop_source_revision_baseline_invalid'; END IF;
  INSERT INTO public.netshop_source_revision_markers
    (transaction_id,baseline_revision,baseline_digest)
    VALUES (current_transaction,prior_revision,prior_digest);
  RETURN NULL;
END $$;
-- STEP --

CREATE FUNCTION public.netshop_source_revision_required_at_commit() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE current_revision bigint; current_digest text;
BEGIN
  IF NEW.transaction_id IS DISTINCT FROM txid_current()
  THEN RAISE EXCEPTION 'netshop_source_revision_marker_not_owned'; END IF;
  SELECT revision,source_digest INTO current_revision,current_digest
    FROM public.netshop_data_revisions WHERE domain='netshop' FOR UPDATE;
  IF NOT FOUND OR current_revision IS NULL
     OR current_revision<=NEW.baseline_revision
     OR current_revision>9007199254740991
     OR current_digest IS NULL OR current_digest !~ '^[0-9a-f]{64}$'
     OR current_digest=NEW.baseline_digest
  THEN RAISE EXCEPTION 'netshop_source_write_without_revision'; END IF;
  DELETE FROM public.netshop_source_revision_markers
    WHERE transaction_id=NEW.transaction_id;
  RETURN NULL;
END $$;
-- STEP --

REVOKE ALL ON FUNCTION public.netshop_source_mark_revision_required() FROM PUBLIC;
-- STEP --
REVOKE ALL ON FUNCTION public.netshop_source_revision_required_at_commit() FROM PUBLIC;
-- STEP --
REVOKE ALL ON FUNCTION public.netshop_source_revision_monotonic() FROM PUBLIC;
-- STEP --

CREATE TRIGGER netshop_source_revision_monotonic
  BEFORE INSERT OR UPDATE OR DELETE ON public.netshop_data_revisions
  FOR EACH ROW EXECUTE FUNCTION public.netshop_source_revision_monotonic();
-- STEP --
CREATE CONSTRAINT TRIGGER netshop_source_revision_required
  AFTER INSERT ON public.netshop_source_revision_markers
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
  EXECUTE FUNCTION public.netshop_source_revision_required_at_commit();
-- STEP --
CREATE TRIGGER netshop_row_revision_required
  BEFORE INSERT OR UPDATE OR DELETE ON public.netshop_rows
  FOR EACH STATEMENT EXECUTE FUNCTION public.netshop_source_mark_revision_required();
-- STEP --
CREATE TRIGGER netshop_batch_revision_required
  BEFORE INSERT OR UPDATE OR DELETE ON public.netshop_import_batches
  FOR EACH STATEMENT EXECUTE FUNCTION public.netshop_source_mark_revision_required();
"""


UNINSTALL = """
DROP TRIGGER IF EXISTS netshop_row_revision_required ON public.netshop_rows;
DROP TRIGGER IF EXISTS netshop_batch_revision_required ON public.netshop_import_batches;
DROP TRIGGER IF EXISTS netshop_source_revision_required ON public.netshop_source_revision_markers;
DROP TRIGGER IF EXISTS netshop_source_revision_monotonic ON public.netshop_data_revisions;
DROP FUNCTION IF EXISTS public.netshop_source_mark_revision_required();
DROP FUNCTION IF EXISTS public.netshop_source_revision_required_at_commit();
DROP FUNCTION IF EXISTS public.netshop_source_revision_monotonic();
DROP TABLE IF EXISTS public.netshop_source_revision_markers;
"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cursor:
            for statement in INSTALL.split("\n-- STEP --\n"):
                cursor.execute(statement)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT EXISTS(SELECT 1 FROM public.netshop_rows) OR "
                "EXISTS(SELECT 1 FROM public.netshop_import_batches) OR "
                "NOT EXISTS(SELECT 1 FROM public.netshop_write_authority "
                "WHERE id=1 AND status='d1') OR "
                "NOT EXISTS(SELECT 1 FROM public.netshop_data_revisions "
                "WHERE domain='netshop' AND revision=0 AND source_digest=repeat('0',64)) OR "
                "EXISTS(SELECT 1 FROM pg_catalog.pg_stat_activity "
                "WHERE datname=current_database() AND usename='teruisi_netshop_writer' "
                "AND pid<>pg_backend_pid())")
            if cursor.fetchone()[0]:
                raise RuntimeError("存在网店事实、修订或活动writer，不能逆迁移并失去写入门禁")
            for statement in UNINSTALL.split(";"):
                if statement.strip():
                    cursor.execute(statement)


class Migration(migrations.Migration):
    dependencies = [("netshop", "0002_migration_run_time_order")]
    operations = [migrations.RunPython(install, uninstall)]

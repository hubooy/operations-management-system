from django.db import connection, DatabaseError, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class CollectionUpgradeTests(TransactionTestCase):
    def test_old_rows_and_terminal_guard_survive_new_manual_defaults(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        old, new = [("ai_assistant", "0014_business_evidence")], [("ai_assistant", "0015_business_collection")]
        try:
            executor.migrate(old)
            apps = executor.loader.project_state(old).apps
            model = apps.get_model("ai_assistant", "AiBusinessEvidenceRun")
            for index, status in enumerate(("collecting", "sealed")):
                model.objects.create(id=f"legacy-{index}", owner_email="upgrade@example.test", client_request_id=f"legacy-{index}",
                    request_digest="a"*64, plan_json='{"schemaVersion":"business-evidence-v1","sources":[]}', status=status)
            before = list(model.objects.order_by("id").values())
            executor = MigrationExecutor(connection)
            executor.migrate(new)
            from .business_models import AiBusinessEvidenceRun
            old_fields = [field.name for field in model._meta.fields]
            self.assertEqual(list(AiBusinessEvidenceRun.objects.order_by("id").values(*old_fields)), before)
            self.assertEqual(set(AiBusinessEvidenceRun.objects.values_list("collection_status", flat=True)), {"manual"})
            self.assertEqual(set(AiBusinessEvidenceRun.objects.values_list("collection_failures", flat=True)), {0})
            self.assertFalse(AiBusinessEvidenceRun.objects.filter(next_collect_at__isnull=True).exists())
            with self.assertRaises(DatabaseError), transaction.atomic():
                AiBusinessEvidenceRun.objects.filter(pk="legacy-1").update(collection_status="queued", version=2)
        finally:
            MigrationExecutor(connection).migrate(latest)

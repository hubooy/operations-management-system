from copy import deepcopy
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.db import connection, transaction, DatabaseError
from . import prompt_settings as service, models as m, chat
from . import tests as fixtures
from .policy import AiError
from .control_models import AiMutationAudit


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromptSettingsTests(TestCase):
    user = fixtures.AiDomainTests.user
    call = fixtures.AiDomainTests.call

    def setUp(self):
        fixtures.AiDomainTests.setUp(self)
        self.admin = self.user("guidance-admin@example.invalid", "admin", None)
        self.config = deepcopy(service.DEFAULT_CONFIG)

    def save(self, config=None, version=0):
        return service.save({"action": "save", "expectedVersion": version, "config": config or self.config}, self.admin)

    def test_admin_scope_and_unknown_fields_fail_closed(self):
        scoped = self.user("restricted-admin@example.invalid", "admin", self.owner.scope)
        for actor in (self.owner, self.viewer, scoped):
            with self.assertRaises(AiError): service.read(actor, {})
            with self.assertRaises(AiError): service.save({}, actor)
            self.assertEqual(self.call("/api/ai/prompt-settings", {}, actor, method="GET").status_code, 403)
        with self.assertRaises(AiError): service.validate({**self.config, "tools": ["write_inventory"]})
        with self.assertRaises(AiError): service.read(self.admin, {"page": "0"})

    def test_versions_restore_append_history_and_cas(self):
        original = self.save()["item"]
        changed = deepcopy(self.config); changed["globalPrompt"] = "先给简短结论"
        self.save(changed, 1)
        with self.assertRaises(AiError) as error: self.save(self.config, 1)
        self.assertEqual(error.exception.code, "version_conflict")
        restored = service.save({"action": "restore", "expectedVersion": 2, "restoreVersion": 1}, self.admin)["item"]
        self.assertEqual(restored["version"], 3)
        self.assertEqual(restored["config"], original["config"])
        self.assertEqual(service.snapshot(2)["config"], changed)
        self.assertEqual(service.snapshot()["restoredFrom"], 1)
        self.assertEqual(m.AiPromptSettingsRevision.objects.count(), 3)

    def test_signed_api_is_idempotent_and_audited(self):
        body = {"action": "save", "expectedVersion": 0, "config": self.config}
        response = self.call("/api/ai/prompt-settings", body, self.admin, request_id="prompt-save-1")
        self.assertEqual(response.status_code, 200, response.content)
        replay = self.call("/api/ai/prompt-settings", body, self.admin, request_id="prompt-save-1")
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(replay.json(), response.json())
        self.assertEqual(m.AiPromptSettingsRevision.objects.count(), 1)
        self.assertEqual(AiMutationAudit.objects.filter(request_id="prompt-save-1").count(), 1)
        changed = {**body, "config": {**self.config, "commonRules": "changed"}}
        self.assertEqual(self.call("/api/ai/prompt-settings", changed, self.admin, request_id="prompt-save-1").status_code, 409)

    def test_bounds_duplicate_disabled_and_unauthorized_rules(self):
        config = deepcopy(self.config)
        config["rules"].append(config["rules"][0])
        with self.assertRaises(AiError): service.validate(config)
        config = deepcopy(self.config); config["rules"][0]["domains"] = ["credentials"]
        with self.assertRaises(AiError): service.validate(config)
        config = deepcopy(self.config); config["globalPrompt"] = "中" * 8001
        with self.assertRaises(AiError): service.validate(config)
        item = service.snapshot()
        _, evidence = service.compose(item, "库存与毛利", None, [{"name": "get_inventory_health"}])
        self.assertEqual([r["id"] for r in evidence["rules"]], ["inventory-health"])
        item["config"]["rules"][1]["enabled"] = False
        _, evidence = service.compose(item, "库存", None, [{"name": "get_inventory_health"}])
        self.assertEqual(evidence["rules"], [])
        _, evidence = service.compose(service.snapshot(), "继续", {"module": "inventory"}, [{"name": "get_inventory_health"}])
        self.assertEqual(evidence["rules"][0]["id"], "inventory-health")

    def test_history_is_bounded_and_invalid_restore_has_no_write(self):
        for version in range(22): self.save(version=version)
        first = service.read(self.admin, {})
        self.assertEqual(len(first["history"]), 20); self.assertTrue(first["hasMore"])
        second = service.read(self.admin, {"page": "2"})
        self.assertEqual(len(second["history"]), 2); self.assertFalse(second["hasMore"])
        with self.assertRaises(AiError): service.save({"action": "restore", "expectedVersion": 22, "restoreVersion": 99}, self.admin)
        self.assertEqual(m.AiPromptSettingsRevision.objects.count(), 22)

    def test_running_chat_pins_guidance_and_records_evidence(self):
        self.save()
        observed = []
        catalog = deepcopy(fixtures.CATALOG)
        catalog.append({**deepcopy(catalog[0]), "name": "get_inventory_health", "title": "库存健康"})
        def turn(model, frames, system, tools, **kw):
            observed.append(system)
            if len(observed) == 1:
                changed = deepcopy(self.config); changed["globalPrompt"] = "后续新版本"
                self.save(changed, 1)
                return {"text": "", "frame": {"role": "assistant", "content": ""}, "calls": [{"id": "call-stock", "name": "get_inventory_health", "arguments": {}}]}
            return {"text": "已核对库存", "frame": {"role": "assistant", "content": "已核对库存"}, "calls": []}
        with patch("ai_assistant.transport.catalog", return_value=catalog), patch("ai_assistant.transport.execute_tool", return_value={"ok": True, "data": {}}), patch("ai_assistant.provider.turn", side_effect=turn):
            result = chat.answer({"clientRequestId": "pinned-guidance", "message": "库存风险", "workspaceModule": "ai"}, self.other, "pinned-guidance")
        self.assertGreaterEqual(len(observed), 2)
        self.assertTrue(all("后续新版本" not in system for system in observed))
        self.assertEqual(result["execution"]["guidance"]["version"], 1)
        self.assertEqual(result["execution"]["guidance"]["rules"][0]["id"], "inventory-health")
        restored = chat.messages({"conversationId": result["conversationId"]}, self.other)
        self.assertTrue(any(msg.get("execution", {}).get("guidance", {}).get("version") == 1 for msg in restored["items"]))

    def test_postgres_immutable_history_and_size_constraint(self):
        if connection.vendor != "postgresql": self.skipTest("PostgreSQL guard verification")
        self.save()
        for sql in ["UPDATE ai_prompt_settings_revisions SET created_by='changed'", "DELETE FROM ai_prompt_settings_revisions"]:
            with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(sql)
        with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("INSERT INTO ai_prompt_settings_revisions (version,config_json,created_by,created_at) VALUES (2,%s,'test',now())", ["x" * 65537])

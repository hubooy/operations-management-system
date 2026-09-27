"""0008 -> 0009 only in the fresh cluster owned by ai-postgres-rehearsal."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import django
django.setup()
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

database = settings.DATABASES["default"]
if (ROOT == Path(r"D:\运营管理系统") or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1" or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal" or connection.vendor != "postgresql"
        or connection.introspection.table_names()):
    raise RuntimeError("Generation upgrade requires the fresh isolated rehearsal database")
old_target = [("ai_assistant", "0008_dingtalk_settings")]
executor = MigrationExecutor(connection)
executor.migrate(old_target)
old = executor.loader.project_state(old_target).apps
Model = old.get_model("ai_assistant", "AiModels")
Message = old.get_model("ai_assistant", "AiConversationMessages")
Model.objects.create(id="upgrade-model", name="Fixture", model_name="fixture", protocol="openai_compatible",
                     model_type="text", status="enabled", max_tokens=8192, timeout_ms=120000,
                     api_key_encrypted="opaque-isolated-fixture", api_key_suffix="ture")
Message.objects.create(id="upgrade-message", conversation_id="upgrade-conversation", role="assistant", content="原始历史 **内容**", ordinal=1)
before_model = Model.objects.values().get()
before_message = Message.objects.values().get()
target = [("ai_assistant", "0009_model_generation_capabilities")]
executor = MigrationExecutor(connection)
assert executor.migration_plan(target)
executor.migrate(target)
current = executor.loader.project_state(target).apps
after_model = current.get_model("ai_assistant", "AiModels").objects.values().get()
after_message = current.get_model("ai_assistant", "AiConversationMessages").objects.values().get()
assert {k:after_model[k] for k in before_model} == before_model
assert {k:after_message[k] for k in before_message} == before_message
assert after_model["generation_options_json"] == "{}" and after_message["execution_json"] == "{}"
executor = MigrationExecutor(connection)
assert executor.migration_plan(target) == []
executor.migrate(target)
print(json.dumps({"upgrade":"0008->0009", "existingModelAndMessagePreserved":True,
                  "newFieldsDefaultEmpty":True, "secondApplyNoop":True, "productionWrites":False}))

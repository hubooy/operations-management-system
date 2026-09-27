from importlib import import_module
from django.db import migrations


def change_constraint(schema_editor, versions):
    import_module('ai_assistant.migrations.0017_business_file_renderer').change_constraint(schema_editor, versions)


def allow_excel_renderer(apps, schema_editor):
    change_constraint(schema_editor, '1,2,3')


def restore_offline_constraint(apps, schema_editor):
    if apps.get_model('ai_assistant', 'AiBusinessFileRun').objects.filter(renderer_version=3).exists():
        raise RuntimeError('存在不可改写的 v3 文件任务，不能回退渲染约束')
    change_constraint(schema_editor, '1,2')


class Migration(migrations.Migration):
    dependencies = [('ai_assistant', '0017_business_file_renderer')]
    operations = [migrations.RunPython(allow_excel_renderer, restore_offline_constraint)]

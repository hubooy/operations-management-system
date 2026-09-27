from django.conf import settings
from django.urls import path
from django.views.decorators.http import require_GET

from . import import_chain_views, operations_views, views


read_patterns = [
    path("import-chain-status", import_chain_views.today_status, name="workflow-import-chain-status"),
    path("launch-projects", views.launch_projects, name="workflow-launch-projects"),
    path("launch-projects/<uuid:project_id>", views.launch_project, name="workflow-launch-project"),
    path("new-product-lines", views.new_product_lines, name="workflow-new-product-lines"),
    path("new-product-lines/learn", require_GET(views.new_product_line_learning), name="workflow-new-product-line-preview"),
    path("new-product-lines/<uuid:line_id>/image", views.new_product_line_image, name="workflow-new-product-line-image"),
    path("new-product-weekly-followup", views.new_product_weekly_followup, name="workflow-new-product-weekly-followup"),
    path("new-product-weekly-report-config", views.new_product_weekly_report_config, name="workflow-new-product-weekly-report-config"),
    path("consumers/query", views.consumer_query, name="workflow-consumer-query"),
    path("tasks", operations_views.tasks, name="workflow-tasks"),
    path("tasks/<str:task_id>/collaboration", operations_views.task_collaboration, name="workflow-task-collaboration"),
    path("tasks/<str:task_id>/comments", operations_views.task_comments, name="workflow-task-comments"),
    path("tasks/<str:task_id>/activity", operations_views.task_activity, name="workflow-task-activity"),
    path("tasks/<str:task_id>/reminders", operations_views.task_reminders, name="workflow-task-reminders"),
    path("tasks/<str:task_id>/links", operations_views.task_links, name="workflow-task-links"),
    path("tasks/<str:task_id>/attachments", operations_views.task_attachments, name="workflow-task-attachments"),
    path("tasks/<str:task_id>/attachments/<str:attachment_id>", operations_views.task_attachment, name="workflow-task-attachment"),
    path("templates", operations_views.templates, name="workflow-templates"),
    path("operations-records", operations_views.operation_records, name="workflow-operation-records"),
    path("operations-records/<str:record_id>", operations_views.operation_record, name="workflow-operation-record"),
    path("operations-records/<str:record_id>/activity", operations_views.operation_record_activity, name="workflow-operation-record-activity"),
]
write_patterns = [
    path("launch-projects", views.launch_projects, name="workflow-launch-projects"),
    path("launch-projects/<uuid:project_id>", views.launch_project, name="workflow-launch-project"),
    path("new-product-lines", views.new_product_lines, name="workflow-new-product-lines"),
    path("new-product-lines/<uuid:line_id>", views.new_product_line, name="workflow-new-product-line"),
    path("new-product-lines/learn", views.new_product_line_learning, name="workflow-new-product-line-learning"),
    path("new-product-weekly-report-config", views.new_product_weekly_report_config, name="workflow-new-product-weekly-report-config"),
    path(
        "launch-projects/<uuid:project_id>/stages/<str:stage_key>",
        views.launch_project_stage,
        name="workflow-launch-project-stage",
    ),
    path("tasks", operations_views.tasks, name="workflow-tasks-write"),
    path("tasks/<str:task_id>/comments", operations_views.task_comments, name="workflow-task-comments-write"),
    path("tasks/<str:task_id>/reminders", operations_views.task_reminders, name="workflow-task-reminders-write"),
    path("tasks/<str:task_id>/links", operations_views.task_links, name="workflow-task-links-write"),
    path("tasks/<str:task_id>/attachments", operations_views.task_attachments, name="workflow-task-attachments-write"),
    path("tasks/<str:task_id>/attachments/<str:attachment_id>", operations_views.task_attachment, name="workflow-task-attachment-write"),
    path("attachment-cleanup", operations_views.attachment_cleanup, name="workflow-attachment-cleanup"),
    path("templates", operations_views.templates, name="workflow-templates-write"),
    path("operations-records", operations_views.operation_records, name="workflow-operation-records-write"),
    path("operations-records/<str:record_id>", operations_views.operation_record, name="workflow-operation-record-write"),
    path("inventory-work-items", operations_views.inventory_work_item, name="workflow-inventory-work-items"),
]

urlpatterns: list[object] = []
if settings.DJANGO_PROCESS_ROLE == "workflow_reader":
    urlpatterns.extend(read_patterns)
elif settings.DJANGO_PROCESS_ROLE == "workflow_writer":
    urlpatterns.extend(write_patterns)
elif settings.DJANGO_PROCESS_ROLE == "development":
    urlpatterns.extend([pattern for pattern in read_patterns if pattern.name != "workflow-new-product-line-preview"] + write_patterns)

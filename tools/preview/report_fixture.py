"""Synthetic report UI evidence. Called only by the isolated preview seeder."""
from datetime import timedelta
import os
from pathlib import Path


def seed_report(today):
    from django.conf import settings
    from ai_assistant import models as m, report_library
    from ai_assistant.policy import canonical, digest
    root = Path(__file__).resolve().parents[2]
    database = Path(settings.DATABASES["default"]["NAME"]).resolve()
    if settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3" or database.parent != root / ".runtime/preview" or os.getenv("TERUISI_DJANGO_DATABASE_URL"):
        raise RuntimeError("Report fixture requires this worktree's synthetic preview SQLite")
    scope = {"platform": "京东", "shop": "合成演示店铺", "startDate": str(today-timedelta(days=7)), "endDate": str(today-timedelta(days=1))}
    template = report_library.DEFAULTS["templates"][0]
    pipeline = {**report_library.DEFAULTS["pipelines"][0], "name": "网店经营周报 · 合成演示"}
    sections = [{"title": title, "body": body} for title, body in zip(template["sections"], [
        "本报告仅用于页面与文件排版演示，不代表实际经营结果。\n演示期间成交金额为 28,600 元。DEMO-002 需要优先核验商品信息与转化表现。",
        "来源：独立预览环境生成的合成数据。\n范围：" + scope["startDate"] + " 至 " + scope["endDate"] + "（含首尾日），京东 / 合成演示店铺。\n示例包含两个 SKU；不推断全店去重访客。实际报告须检查权威覆盖。",
        "DEMO-001：成交金额 18,600 元。\nDEMO-002：成交金额 10,000 元。\n合计 28,600 元，仅为合成样例。未提供完整环比数据，因此不计算增长率。",
        "演示观察：DEMO-002 的转化表现需要复核。\n可能原因：商品信息、流量结构或价格变化；本样例没有验证这些原因。",
        "1. 运营人员核验异常 SKU 的商品信息、价格与同期流量。\n2. 核对数据截止时间，再决定是否补充比较区间。\n3. 复核完成后下载报告；不会自动调整商品、投放预算或发送通知。",
    ])]
    graph = {"nodes": [
        {"key": "collect", "type": "agent", "dependsOn": [], "instruction": "合成取数"},
        {"key": "report", "type": "agent", "dependsOn": ["collect"], "instruction": "合成报告"},
        {"key": "review", "type": "human_review", "dependsOn": ["report"], "instruction": "合成复核"},
    ]}
    flow = m.AiWorkflowRuns.objects.create(id="preview-report-flow", owner_email="local-admin@teruisi.local", client_request_id="preview-report-flow",
        request_digest=digest(graph), scope_json="null", name=pipeline["name"], graph_json=canonical(graph), graph_digest=digest(graph),
        input_json=canonical(scope), status="completed")
    for i, node in enumerate(graph["nodes"]):
        m.AiWorkflowNodeRuns.objects.create(id="preview-report-node-"+str(i), run=flow, node_key=node["key"], node_type=node["type"], position=i,
            instruction=node["instruction"], depends_on_json=canonical(node["dependsOn"]), status="completed",
            output_json=canonical({"answer": canonical({"sections": sections})}) if i == 1 else canonical({"synthetic": True}))
    report = m.AiReportRun.objects.create(id="preview-report", owner_email=flow.owner_email, scope_json="null", client_request_id="preview-report",
        request_digest=digest(scope), workflow=flow, snapshot_json=canonical({"libraryVersion": 0, "libraryDigest": digest(report_library.DEFAULTS),
            "scope": scope, "template": template, "pipeline": pipeline, "skills": [report_library.DEFAULTS["skills"][0]]}))
    job = m.AiAgentJobs.objects.create(id="preview-report-agent", owner_email=flow.owner_email, client_request_id="preview-report-agent",
        request_digest=digest(scope), scope_json="null", task="合成取数，无模型调用", workflow_run_id=flow.id, status="completed")
    provider = m.AiAgentProviderDispatches.objects.create(id="preview-report-provider", job=job, dispatch_ordinal=1, owner_email=flow.owner_email,
        actor_role="admin", model_id="synthetic-no-provider", model_version=1, tool_policy_digest=digest([]), request_digest=digest(scope), lease_epoch=1, state="succeeded")
    for i, (tool, args, value) in enumerate([
        ("get_data_freshness", {}, {"ok": True, "data": {"synthetic": True, "coveredThrough": scope["endDate"]}}),
        ("get_netshop_performance", scope, {"ok": True, "data": {"synthetic": True, "truncated": False, "items": [
            {"SKU": "DEMO-001", "成交金额（元）": 18600}, {"SKU": "DEMO-002", "成交金额（元）": 10000}]}}),
    ]):
        dispatch = m.AiAgentToolDispatches.objects.create(id="preview-report-tool-"+str(i), job=job, provider_dispatch=provider,
            tool_call_ordinal=i+1, provider_call_id="synthetic-"+str(i), tool_name=tool, arguments_json=canonical(args), arguments_digest=digest(args),
            invocation_id="preview-report-invocation-"+str(i), lease_epoch=1, state="succeeded")
        m.AiAgentToolResults.objects.create(tool_dispatch=dispatch, result_json=canonical(value), result_digest=digest(value))
    return report

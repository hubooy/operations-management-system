"""Reports bind immutable templates to the existing durable, reviewed DAG."""
import json
from datetime import date
from . import models as m, report_library as library, workflows
from .policy import AiError, authorize_owner, canonical, current_principal, digest, fields, identifier, mutation, owned, page, text, uid


def run_snapshot(row):
    return json.loads(row.snapshot_json)


def mapping(row):
    data = run_snapshot(row)
    flow = row.workflow
    return {"id": row.id, "name": data["pipeline"]["name"], "createdAt": row.created_at.isoformat(),
            "status": flow.status, "dryRun": bool(flow.dry_run), "workflowId": flow.id,
            "version": data["libraryVersion"], "scope": data["scope"], "template": data["template"]["name"],
            "format": data["template"]["format"], "skills": [s["name"] for s in data["skills"]]}


def get(report_id, principal):
    current_principal(principal)
    row = m.AiReportRun.objects.select_related("workflow").filter(pk=identifier(report_id)).first()
    if not row:
        raise AiError("报告不存在", "not_found", 404)
    authorize_owner(row, principal)
    authorize_owner(row.workflow, principal)
    return row


def listing(params, principal):
    fields(params, {"page", "pageSize"})
    return page(owned(m.AiReportRun.objects.select_related("workflow").all(), principal).order_by("-created_at", "id"), params, mapper=mapping)


def scope(value):
    fields(value, {"platform", "shop", "startDate", "endDate"}, {"platform", "shop", "startDate", "endDate"})
    result = {k: text(value[k], k, 100 if k in {"shop", "platform"} else 10) for k in value}
    try:
        start, end = date.fromisoformat(result["startDate"]), date.fromisoformat(result["endDate"])
    except ValueError as error:
        raise AiError("日期格式无效") from error
    if start.isoformat() != result["startDate"] or end.isoformat() != result["endDate"] or not 0 <= (end-start).days <= 92:
        raise AiError("报告日期需为连续1–93天")
    return result


def create(body, principal):
    current_principal(principal, admin=True, write=True)
    fields(body, {"clientRequestId", "pipelineId", "expectedVersion", "scope", "dryRun"}, {"clientRequestId", "pipelineId", "expectedVersion", "scope", "dryRun"})
    client = identifier(body["clientRequestId"])
    if type(body["dryRun"]) is not bool or type(body["expectedVersion"]) is not int:
        raise AiError("空跑或版本字段无效")
    filters = scope(body["scope"])
    request_digest = digest(body)
    with mutation(principal):
        old = m.AiReportRun.objects.select_related("workflow").filter(owner_email=principal.email.lower(), client_request_id=client).first()
        if old:
            authorize_owner(old, principal)
            if old.request_digest != request_digest:
                raise AiError("请求标识已绑定其他报告", "conflict", 409)
            return {"item": mapping(old), "replayed": True}
        version = library.snapshot()
        if version["version"] != body["expectedVersion"]:
            raise AiError("资源库已更新，请刷新后启动", "version_conflict", 409)
        config = version["config"]
        pipeline = next((p for p in config["pipelines"] if p["id"] == body["pipelineId"] and p["enabled"]), None)
        if not pipeline:
            raise AiError("流水线未启用", "conflict", 409)
        template = next((t for t in config["templates"] if t["id"] == pipeline["templateId"] and t["enabled"]), None)
        skills = [s for s in config["skills"] if s["id"] in pipeline["skillIds"] and s["enabled"]]
        if not template or len(skills) != len(pipeline["skillIds"]):
            raise AiError("关联模板或技能已停用", "conflict", 409)
        snapshot_json = canonical({"libraryVersion": version["version"], "libraryDigest": digest(config),
            "template": template, "skills": skills, "pipeline": pipeline, "scope": filters})
        if len(snapshot_json.encode()) > 32768:
            raise AiError("本次模板与技能快照超过32 KiB，请精简正文", "payload_too_large", 413)
        active = m.AiReportRun.objects.select_related("workflow").filter(owner_email=principal.email.lower(),
            scope_json=canonical(principal.scope), snapshot_json=snapshot_json,
            workflow__status__in=workflows.ACTIVE, workflow__dry_run=int(body["dryRun"])).first()
        if active:
            return {"item": mapping(active), "replayed": True}
        specification = {"sections": [{"title": title, "body": "有依据的文字，包含必要数值及单位"} for title in template["sections"]]}
        graph = {"nodes": [
            {"key": "collect", "type": "agent", "dependsOn": [], "instruction": "为报告核验并获取数据。严格使用workflowInput内的平台、店铺或仓别、起止日期（含首尾日）；先查询覆盖，再查询已授权业务工具，不使用其他范围替代。输出不超过1800字的事实、单位、来源、完整性与缺口；事实不足也如实返回。取数方法：" + template["dataSteps"]},
            {"key": "report", "type": "agent", "dependsOn": ["collect"], "instruction": "根据已取得的事实生成报告，缺失不能补造。仅输出JSON，无代码块；严格使用下列章节名称和顺序，每章body为字符串，合计不超过10000字。结构：" + canonical(specification) + "\n写作要求：" + template["writingRules"]},
            {"key": "review", "type": "human_review", "dependsOn": ["report"], "instruction": "核对报告范围、数据完整性、取数证据与结论。通过后才可导出正式报告及确认通知；拒绝不自动重跑模型。"},
        ]}
        result = workflows.create({"clientRequestId": "report-"+digest([principal.email.lower(), client]), "name": pipeline["name"], "graph": graph,
            "input": filters, "dryRun": body["dryRun"]}, principal, True, skill_ids=pipeline["skillIds"], library_snapshot=version)
        row = m.AiReportRun.objects.create(id=uid("ai-report"), owner_email=principal.email.lower(), scope_json=canonical(principal.scope),
            client_request_id=client, request_digest=request_digest, workflow_id=result["item"]["id"],
            snapshot_json=snapshot_json)
    return {"item": mapping(row), "replayed": False}


def content(row, principal=None):
    if run_snapshot(row).get("schemaVersion") == "business-report-v1":
        from . import business_reports
        return business_reports.content(row, principal)["sections"]
    if row.workflow.dry_run:
        raise AiError("空跑只校验流程，不生成报告", "conflict", 409)
    node = m.AiWorkflowNodeRuns.objects.filter(run_id=row.workflow_id, node_key="report", status="completed").first()
    if not node:
        raise AiError("报告正文尚未完成", "conflict", 409)
    answer = json.loads(node.output_json).get("answer", "")
    if answer.startswith("```json") and answer.endswith("```"):
        answer = answer[7:-3].strip()
    try:
        parsed = json.loads(answer)
    except (ValueError, TypeError) as error:
        raise AiError("模型报告结构不符合模板；保留原结果供复核，不自动重跑", "conflict", 409) from error
    fields(parsed, {"sections"}, {"sections"})
    sections = parsed["sections"]
    expected = run_snapshot(row)["template"]["sections"]
    if not isinstance(sections, list) or len(sections) != len(expected):
        raise AiError("报告章节与模板不一致", "conflict", 409)
    for section, title in zip(sections, expected):
        fields(section, {"title", "body"}, {"title", "body"})
        if section["title"] != title:
            raise AiError("报告章节与模板不一致", "conflict", 409)
        section["body"] = text(section["body"], "报告章节正文", 10000)
    return sections


def detail(report_id, principal):
    row = get(report_id, principal)
    result = {"item": mapping(row), "snapshot": run_snapshot(row), "workflow": workflows.mapping(row.workflow)}
    from .business_screening_runtime_contract import PROFILE as screening_profile
    if result["snapshot"].get("executionProfile") == screening_profile:
        from .business_screening_readiness import preparation_status
        result["screeningPreparation"] = preparation_status(row,principal)
    try:
        if run_snapshot(row).get("schemaVersion") == "business-report-v1":
            from . import business_reports
            result.update(business_reports.content(row, principal))
        else:
            result["sections"] = content(row, principal)
    except AiError as error:
        result["contentError"] = str(error)
    delivery = m.AiReportDelivery.objects.filter(report_id=row.id).first()
    result["delivery"] = {"status": delivery.status, "channelId": delivery.channel_id} if delivery else None
    return result


def evidence(row):
    """Read only this workflow's persisted tool receipts, never execute a query on download."""
    results = m.AiAgentToolResults.objects.filter(tool_dispatch__job__workflow_run_id=row.workflow_id).exclude(tool_dispatch__tool_name__startswith="describe_").select_related("tool_dispatch").order_by("tool_dispatch__reserved_at")[:81]
    output, size = [], 0
    for item in results:
        dispatch = item.tool_dispatch
        if dispatch.tool_name.startswith("describe_"):
            continue
        size += len(item.result_json.encode())
        if size > 1024*1024 or len(output) >= 80:
            raise AiError("取数证据超过导出上限，请缩小后续报告范围；当前结果保留", "payload_too_large", 413)
        output.append({"tool": dispatch.tool_name, "arguments": json.loads(dispatch.arguments_json),
            "digest": item.result_digest, "result": json.loads(item.result_json)})
    return output


def verify_sources(sources):
    successful = [s for s in sources if s["result"].get("ok") is True and s["result"].get("auditStatus") != "unavailable"]
    if not any(s["tool"] == "get_data_freshness" for s in successful):
        raise AiError("缺少成功的数据覆盖检查，不能通过复核或交付正式报告", "conflict", 409)
    if not any(s["tool"] not in {"get_data_freshness"} and not s["tool"].startswith("describe_") for s in successful):
        raise AiError("缺少成功的业务取数证据，不能交付正式报告", "conflict", 409)


def validate_review(workflow_id, principal):
    row = m.AiReportRun.objects.select_related("workflow").filter(workflow_id=workflow_id).first()
    if row:
        authorize_owner(row, principal)
        if run_snapshot(row).get("schemaVersion") == "business-report-v1":
            from . import business_reports
            business_reports.validate_review(row, principal)
            return
        content(row, principal)
        verify_sources(evidence(row))


def download(report_id, params, principal):
    from .report_render import render
    fields(params, {"format", "draft"}, {"format"})
    if params["format"] not in {"html", "xlsx"} or params.get("draft", "false") not in {"true", "false"}:
        raise AiError("导出参数无效")
    row = get(report_id, principal)
    draft = params.get("draft") == "true"
    if not draft and row.workflow.status != "completed":
        raise AiError("正式报告须先通过人工复核", "conflict", 409)
    if run_snapshot(row).get("schemaVersion") == "business-report-v1":
        raise AiError("经营分析的完整文件交付尚未接通，请查看结构化结果", "conflict", 409)
    sections, sources = content(row, principal), evidence(row)
    if not draft:
        verify_sources(sources)
    return render(row.id, run_snapshot(row), sections, sources, params["format"], draft)


def send(report_id, body, principal):
    from . import channels
    current_principal(principal, admin=True, write=True)
    fields(body, {"channelId", "targetDigest"}, {"channelId", "targetDigest"})
    channel_id = identifier(body["channelId"])
    row = get(report_id, principal)
    if row.workflow.status != "completed" or row.workflow.dry_run:
        raise AiError("报告尚未通过复核", "conflict", 409)
    validate_review(row.workflow_id, principal)
    with mutation(principal):
        current_principal(principal, admin=True, write=True)
        if m.AiReportDelivery.objects.filter(report_id=row.id).exists():
            raise AiError("此报告已有投递记录，禁止重复发送或重放未知结果", "conflict", 409)
        channel = m.AiChannels.objects.filter(id=channel_id, status="enabled", send_enabled=1).first()
        if not channel:
            raise AiError("通知渠道不可用", "conflict", 409)
        target_digest = channels.target_digest(channel)
        if body["targetDigest"] != target_digest:
            raise AiError("通知渠道配置已变化，请重新选择后确认", "conflict", 409)
        m.AiReportDelivery.objects.create(report_id=row.id, channel_id=channel_id, channel_digest=target_digest)
    try:
        filters = run_snapshot(row)["scope"]
        notice = f"报告已复核：{run_snapshot(row)['pipeline']['name']}\n{filters['platform']} · {filters['shop']}\n{filters['startDate']} 至 {filters['endDate']}\n报告编号：{row.id}\n请登录运营系统 AI流水线查看与下载。"
        channels.send({"id": channel_id, "action": "send", "text": notice}, principal, expected_target_digest=target_digest)
    except Exception:
        with mutation():
            m.AiReportDelivery.objects.filter(report_id=row.id, status="reserved").update(status="unknown")
        raise AiError("通知结果未确认，已保留投递记录，不自动重发", "conflict", 409)
    with mutation(principal):
        m.AiReportDelivery.objects.filter(report_id=row.id, status="reserved").update(status="sent")
    return {"ok": True}

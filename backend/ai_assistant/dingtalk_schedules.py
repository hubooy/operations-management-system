"""Administrator-defined Shanghai schedules; no missed-slot replay or ambiguous resend."""
import base64
from datetime import datetime, timedelta, timezone as utc
from zoneinfo import ZoneInfo

from django.utils import timezone
from sales.auth import Principal

from . import chat, dingtalk, dingtalk_settings, models as m, reports, scheduled_page_capture
from .policy import AiError, current_principal, fields, identifier, integer, mutation, text, uid, digest, canonical

SHANGHAI = ZoneInfo("Asia/Shanghai")


def next_slot(cadence, hour, minute, day, after):
    local = after.astimezone(SHANGHAI)
    for offset in range(0, 33):
        date = local.date() + timedelta(days=offset)
        if cadence == "weekly" and date.isoweekday() != day:
            continue
        if cadence == "monthly" and date.day != day:
            continue
        candidate = datetime(date.year, date.month, date.day, hour, minute, tzinfo=SHANGHAI)
        if candidate > local:
            return candidate.astimezone(utc.utc)
    raise AiError("无法计算下次执行时间")


def record(row):
    return {"id": row.id, "name": row.name, "prompt": row.prompt,
        "contentType": row.content_type, "sourceRef": row.source_ref,
        "cadence": row.cadence, "hour": row.hour, "minute": row.minute, "day": row.day,
        "targetType": row.target_type, "targetId": row.target_id, "senderId": row.sender_id,
        "enabled": row.enabled, "version": row.version,
        "nextRunAt": row.next_run_at.isoformat() if row.next_run_at else None,
        "lastRunAt": row.last_run_at.isoformat() if row.last_run_at else None}


def listing(principal):
    current_principal(principal, admin=True)
    items = list(m.AiDingTalkSchedule.objects.order_by("-updated_at")[:100])
    ids = [item.id for item in items]
    runs = m.AiDingTalkScheduleRun.objects.filter(schedule_id__in=ids).order_by("-created_at")[:100]
    return {"items": [record(item) for item in items], "runs": [
        {"id": run.id, "scheduleId": run.schedule_id, "scheduledAt": run.scheduled_at.isoformat(),
         "status": run.status, "errorCode": run.error_code, "completedAt": run.completed_at.isoformat() if run.completed_at else None}
        for run in runs]}


def save(body, principal):
    current_principal(principal, admin=True)
    required = {"name", "prompt", "cadence", "hour", "minute", "day", "targetType", "targetId", "senderId", "enabled"}
    fields(body, required | {"id", "expectedVersion", "contentType", "sourceRef"}, required)
    name = text(body["name"], "名称", 100)
    content_type = body.get("contentType", "text")
    source_ref = body.get("sourceRef", "")
    if not isinstance(content_type, str):
        raise AiError("投递内容类型无效")
    if content_type == "text":
        prompt = text(body["prompt"], "执行内容", 4000)
        if source_ref != "":
            raise AiError("文本任务不能指定文件或页面")
    elif content_type in {"screenshot", "report_file"}:
        if not isinstance(source_ref, str):
            raise AiError("媒体来源无效")
        prompt = text(body["prompt"], "附带文案", 4000, empty=True)
        if content_type == "report_file" and prompt:
            raise AiError("文件任务不执行 AI 指令")
        source_ref = identifier(source_ref) if content_type == "report_file" else source_ref
        if content_type == "screenshot" and source_ref not in scheduled_page_capture.PAGES:
            raise AiError("截图页面不在允许列表")
        if content_type == "report_file":
            report = reports.get(source_ref, principal)
            if report.workflow.status != "completed" or report.workflow.dry_run:
                raise AiError("报告尚未完成复核，不能定时投递", "conflict", 409)
    else:
        raise AiError("投递内容类型无效")
    cadence = body["cadence"]
    if cadence not in ("daily", "weekly", "monthly"):
        raise AiError("周期无效")
    hour, minute, day = (integer(body[k], k, *bounds) for k, bounds in
        (("hour", (0, 23)), ("minute", (0, 59)), ("day", (1, 28))))
    if cadence == "weekly" and day > 7:
        raise AiError("星期必须在 1–7 之间")
    if body["targetType"] not in ("group", "person") or type(body["enabled"]) is not bool:
        raise AiError("目标或开关无效")
    target = dingtalk.opaque(body["targetId"], "目标 ID", 256)
    sender = dingtalk.opaque(body["senderId"], "执行账号 staffId")
    if body["targetType"] == "person" and target != sender:
        raise AiError("个人投递只允许执行账号本人", "access_denied", 403)
    if body["targetType"] == "group":
        config = dingtalk_settings.read(principal)["config"]
        if not config["configured"] or not config["enabled"] or target not in {g["id"] for g in config["groups"] if g["enabled"]}:
            raise AiError("目标群尚未批准并启用", "access_denied", 403)
    now = timezone.now()
    with mutation(principal):
        if "id" in body:
            row = m.AiDingTalkSchedule.objects.select_for_update().filter(pk=identifier(body["id"])).first()
            if not row:
                raise AiError("任务不存在", "not_found", 404)
            if integer(body.get("expectedVersion"), "expectedVersion") != row.version:
                raise AiError("任务已被修改，请刷新", "version_conflict", 409)
            row.version += 1
        else:
            if "expectedVersion" in body or m.AiDingTalkSchedule.objects.count() >= 100:
                raise AiError("任务数量已达上限或版本无效")
            row = m.AiDingTalkSchedule(id=uid("ding-schedule"), owner_email=principal.email.lower())
        row.name, row.prompt, row.cadence = name, prompt, cadence
        row.content_type, row.source_ref = content_type, source_ref
        row.hour, row.minute, row.day = hour, minute, day
        row.target_type, row.target_id, row.sender_id = body["targetType"], target, sender
        row.enabled, row.updated_at = body["enabled"], now
        row.next_run_at = next_slot(cadence, hour, minute, day, now) if row.enabled else None
        row.save()
    return {"item": record(row)}


def run_now(body, principal):
    current_principal(principal, admin=True)
    fields(body, {"id", "expectedVersion"}, {"id", "expectedVersion"})
    with mutation(principal):
        row = m.AiDingTalkSchedule.objects.select_for_update().filter(pk=identifier(body["id"])).first()
        if not row or not row.enabled:
            raise AiError("任务未启用或不存在", "not_found", 404)
        if row.version != integer(body["expectedVersion"], "expectedVersion"):
            raise AiError("任务版本已变化", "version_conflict", 409)
        if m.AiDingTalkScheduleRun.objects.filter(schedule=row, status__in=["queued", "running", "ready", "sending"]).exists():
            raise AiError("任务已有待执行项", "conflict", 409)
        scheduled_at = timezone.now()
        previous = m.AiDingTalkScheduleRun.objects.filter(schedule=row).order_by("-scheduled_at").values_list("scheduled_at", flat=True).first()
        if previous and scheduled_at <= previous:
            scheduled_at = previous + timedelta(microseconds=1)
        run = m.AiDingTalkScheduleRun.objects.create(id=uid("ding-run"), schedule=row,
            schedule_version=row.version, scheduled_at=scheduled_at, status="queued")
    return {"id": run.id, "status": run.status}


def recover_interrupted():
    with mutation():
        m.AiDingTalkScheduleRun.objects.filter(status__in=["running", "ready", "sending"]).update(
            status="unknown", error_code="interrupted_result_unknown", completed_at=timezone.now())


def step(config_reader, sender, media_sender=None):
    """Called by the singleton Stream receiver; a slot is never replayed after dispatch."""
    if not config_reader()["enabled"]:
        return False
    with mutation():
        now = timezone.now()
        run = m.AiDingTalkScheduleRun.objects.select_for_update().filter(status="queued").order_by("created_at").first()
        if run:
            if now - run.scheduled_at > timedelta(minutes=5):
                run.status, run.error_code, run.completed_at = "denied", "missed_window", now
                run.save(update_fields=["status", "error_code", "completed_at"])
                return True
            run.status = "running"
            run.save(update_fields=["status"])
        else:
            row = m.AiDingTalkSchedule.objects.select_for_update().filter(enabled=True, next_run_at__lte=now).order_by("next_run_at").first()
            if row is None:
                return False
            slot = row.next_run_at
            row.next_run_at = next_slot(row.cadence, row.hour, row.minute, row.day, now)
            row.save(update_fields=["next_run_at"])
            if now - slot > timedelta(minutes=5):
                return True  # No backlog on receiver downtime or disabled policy.
            run, created = m.AiDingTalkScheduleRun.objects.get_or_create(schedule=row, scheduled_at=slot,
                defaults={"id": uid("ding-run"), "schedule_version": row.version, "status": "running"})
            if not created:
                return True
    row = run.schedule
    version, target = run.schedule_version, (row.target_type, row.target_id, row.sender_id)
    def live():
        row.refresh_from_db()
        if not row.enabled or row.version != version or (row.target_type, row.target_id, row.sender_id) != target:
            raise AiError("任务配置已变化", "access_denied", 403)
        current_principal(Principal(row.owner_email, row.owner_email, "admin", None), admin=True, background=True)
        config = config_reader()
        if row.target_type == "group" and row.target_id not in {g["id"] for g in config["groups"]}:
            raise AiError("目标群已撤销", "access_denied", 403)
        return config, dingtalk.principal_for(config, row.sender_id)
    sending = False
    try:
        config, principal = live()
        if row.content_type != "text" and (principal.email.lower() != row.owner_email.lower() or principal.role != "admin" or principal.scope is not None):
            raise AiError("媒体任务只允许创建管理员本人无范围限制的绑定账号执行", "access_denied", 403)
        session_key = digest(["schedule", config, row.id, version, row.sender_id, row.target_type, row.target_id, principal.email, principal.scope])
        with mutation(principal):
            session, _ = m.AiDingTalkSession.objects.get_or_create(pk=session_key, defaults={
                "config_digest": digest(config), "corp_id": config["corpId"], "robot_code": config["robotCode"],
                "sender_id": row.sender_id, "conversation_type": "2" if row.target_type == "group" else "1",
                "external_conversation_id": row.target_id, "owner_email": principal.email,
                "scope_json": canonical(principal.scope)})
        def channel_guard():
            live()
            return dingtalk.guard(session, config_reader())
        if row.content_type == "text":
            answer = chat.answer({"clientRequestId": "ding-scheduled-" + run.id, "message": row.prompt,
                "conversationId": session.conversation_id, "workspaceModule": "ai", "title": "志高助手 · 定时任务"},
                principal, run.id, dingtalk_session=session, channel_guard=channel_guard, channel_time=run.scheduled_at)
            content = dingtalk.plain_reply(answer["reply"])
            attachment = None
        elif row.content_type == "screenshot":
            content = row.prompt
            attachment = (scheduled_page_capture.capture(row.source_ref, row.owner_email),
                          "系统页面-" + row.source_ref.replace(":", "-") + ".png", "image")
        else:
            result = reports.download(row.source_ref, {"format": "xlsx"}, principal)
            attachment = (base64.b64decode(result["base64"], validate=True), "报告-" + run.id + ".xlsx", "file")
        channel_guard()
        with mutation(principal):
            run.status = "ready"
            run.save(update_fields=["status"])
        # Commit the irreversible external-send reservation before calling DWS.
        with mutation(principal):
            run.status = "sending"
            run.save(update_fields=["status"])
        sending = True
        channel_guard()
        if attachment is None:
            sender(session, content, before_send=channel_guard)
        elif media_sender is None:
            raise AiError("机器人媒体投递器不可用", "channel_unavailable", 503)
        else:
            if row.content_type == "screenshot" and content:
                media_sender(session, *attachment, content, before_send=channel_guard)
            else:
                media_sender(session, *attachment, before_send=channel_guard)
        with mutation():
            run.status, run.completed_at = "sent", timezone.now()
            run.save(update_fields=["status", "completed_at"])
            row.last_run_at = run.scheduled_at
            row.save(update_fields=["last_run_at"])
    except AiError as error:
        with mutation():
            run.status = "unknown" if sending or error.code in ("delivery_unknown", "ai_chat_result_unknown") else "denied" if error.status == 403 else "failed"
            run.error_code, run.completed_at = error.code, timezone.now()
            run.save(update_fields=["status", "error_code", "completed_at"])
    except Exception:
        with mutation():
            run.status = "unknown"
            run.error_code, run.completed_at = "execution_result_unknown", timezone.now()
            run.save(update_fields=["status", "error_code", "completed_at"])
    return True

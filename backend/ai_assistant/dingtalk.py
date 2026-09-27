"""Trusted Stream ingress and durable, at-most-once external delivery."""
import json
import re
from datetime import timedelta, datetime, UTC
from pathlib import Path
from django.utils import timezone
from sales.auth import Principal
from . import models as m, chat, transport
from .policy import AiError, canonical, current_principal, digest, fields, mutation, text, valid_scope

ACTIVE = ("queued", "running", "ready", "sending")


def opaque(value, name, maximum=160):
    if not isinstance(value, str) or not value or len(value) > maximum or "," in value or value.startswith("-") or value != value.strip() or any(ord(c) < 33 for c in value):
        raise AiError(name + " 无效")
    return value


def load_config(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 32768:
        raise AiError("钉钉问数配置必须为有界普通文件")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise AiError("钉钉配置包含重复字段")
            result[key] = value
        return result
    config = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique)
    validate_config(config)
    return config


def validate_config(config):
    required = {"version", "enabled", "profile", "corpId", "unifiedAppId", "robotCode", "robotName", "groups", "bindings"}
    fields(config, required | {"policyVersion"}, required)
    if type(config["version"]) is not int or config["version"] not in (1, 2) or type(config["enabled"]) is not bool or config["robotName"] != "志高助手":
        raise AiError("钉钉问数配置版本或机器人名称无效")
    if config["version"] == 2 and (type(config.get("policyVersion")) is not int or config["policyVersion"] < 1):
        raise AiError("AI 群配置版本无效")
    for key in ("profile", "corpId", "unifiedAppId", "robotCode"):
        opaque(config[key], key)
    if config["profile"].split(":")[0] != config["corpId"]:
        raise AiError("钉钉组织与授权 profile 不一致")
    if not isinstance(config["groups"], list) or len(config["groups"]) > 30:
        raise AiError("最多允许 30 个指定群")
    group_ids = set()
    for group in config["groups"]:
        fields(group, {"id", "name"}, {"id", "name"})
        opaque(group["id"], "group", 256)
        text(group["name"], "群名称", 100)
        if group["id"] in group_ids:
            raise AiError("群 ID 不能重复")
        group_ids.add(group["id"])
    bindings = config["bindings"]
    if not isinstance(bindings, list) or not 1 <= len(bindings) <= 30:
        raise AiError("请配置 1–30 个钉钉账号绑定")
    senders = set()
    for item in bindings:
        fields(item, {"senderId", "ownerEmail", "role", "scope"}, {"senderId", "ownerEmail", "role", "scope"})
        sender = opaque(item["senderId"], "senderId")
        if sender in senders:
            raise AiError("钉钉用户绑定重复")
        senders.add(sender)
        email = opaque(item["ownerEmail"], "ownerEmail", 320)
        if "@" not in email or email != email.lower() or item["role"] not in {"analyst", "operator", "admin"} or not valid_scope(item["scope"]) or len(canonical(item["scope"]).encode()) > 16000:
            raise AiError("系统账号、角色或范围快照无效")
    return config


def principal_for(config, sender):
    if not config["enabled"]:
        raise AiError("钉钉问数未启用", "access_denied", 403)
    binding = next((b for b in config["bindings"] if b["senderId"] == sender), None)
    if not binding:
        raise AiError("钉钉用户尚未绑定", "dingtalk_sender_unbound", 403)
    principal = Principal(binding["ownerEmail"], binding["ownerEmail"], binding["role"], binding["scope"])
    return current_principal(principal, write=True, background=True)


def accept(config, data):
    """Called only with SDK-authenticated application messages, never HTTP bodies."""
    validate_config(config)
    if not isinstance(data, dict) or len(canonical(data).encode()) > 32768:
        raise AiError("钉钉消息格式无效", "dingtalk_invalid_envelope")
    if data.get("robotCode") != config["robotCode"] or data.get("senderCorpId") != config["corpId"]:
        raise AiError("钉钉消息应用或组织不匹配", "dingtalk_identity_mismatch", 403)
    sender = opaque(data.get("senderStaffId"), "senderStaffId")
    principal = principal_for(config, sender)
    kind = data.get("conversationType")
    external = opaque(data.get("conversationId"), "conversationId", 256)
    if kind not in ("1", "2") or data.get("msgtype") != "text":
        raise AiError("首批仅支持文本单聊和群内 @ 提问", "dingtalk_unsupported_message")
    if kind == "2" and (data.get("isInAtList") is not True or external not in {g["id"] for g in config["groups"]}):
        raise AiError("只接收指定群内 @ 机器人的提问", "dingtalk_group_not_allowed", 403)
    message_id = opaque(data.get("msgId"), "msgId", 256)
    if not isinstance(data.get("text"), dict):
        raise AiError("文本消息正文无效", "dingtalk_invalid_text")
    prompt = text(data["text"].get("content"), "消息", 4000)
    config_sha = digest(config)
    session_key = digest([config_sha, sender, kind, external, principal.email, principal.scope])
    receipt_key = digest([config["corpId"], config["robotCode"], message_id])
    payload_sha = digest([session_key, prompt])
    with mutation(principal):
        previous = m.AiDingTalkReceipt.objects.filter(pk=receipt_key).first()
        if previous:
            if previous.payload_digest != payload_sha:
                raise AiError("消息 ID 已绑定其他内容", "conflict", 409)
            return previous.id
        created = data.get("createAt")
        now_ms = int(timezone.now().timestamp() * 1000)
        if type(created) is not int or not now_ms - 600000 <= created <= now_ms + 60000:
            raise AiError("消息已过期或时间无效", "dingtalk_message_expired")
        if (m.AiDingTalkReceipt.objects.filter(status__in=ACTIVE).count() >= 24 or
                m.AiDingTalkReceipt.objects.filter(session__sender_id=sender, updated_at__gte=timezone.now()-timedelta(minutes=1)).count() >= 6):
            raise AiError("问数队列繁忙", "ai_chat_quota_exceeded", 429)
        session, _ = m.AiDingTalkSession.objects.get_or_create(pk=session_key, defaults={
            "config_digest": config_sha, "corp_id": config["corpId"], "robot_code": config["robotCode"],
            "sender_id": sender, "conversation_type": kind, "external_conversation_id": external,
            "owner_email": principal.email, "scope_json": canonical(principal.scope),
        })
        m.AiDingTalkReceipt.objects.create(id=receipt_key, session=session, payload_digest=payload_sha, prompt=prompt,
            created_at=datetime.fromtimestamp(created / 1000, UTC))
    return receipt_key


def guard(session, config):
    validate_config(config)
    if session.config_digest != digest(config):
        raise AiError("问数配置已改变，旧请求停止", "access_denied", 403)
    principal = principal_for(config, session.sender_id)
    if principal.email != session.owner_email or canonical(principal.scope) != session.scope_json:
        raise AiError("问数身份或数据范围已改变", "access_denied", 403)
    return principal


def plain_reply(value):
    # Do not cause clients to fetch model-controlled images or links.
    value = text(value, "回复", 48000)
    value = re.sub(r"!\[[^\]]*\]\([^)]*\)", "[图片未外发]", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"(?:https?://|www\.)\S+", "[链接未外发]", value, flags=re.I)
    value = value.replace("@", "＠").replace("<", "＜").replace(">", "＞")
    if len(value) > 3000:
        value = value[:2900] + "\n（回复较长，已截断；请缩小范围继续提问。）"
    return value


def mark(row, status, code=""):
    with mutation():
        row.status, row.error_code, row.updated_at = status, code, timezone.now()
        row.save(update_fields=["status", "error_code", "updated_at"])


def recover_interrupted():
    """Only run while holding the process singleton lock. Never replay paid calls."""
    with mutation():
        for row in m.AiDingTalkReceipt.objects.filter(status="running").select_related("session")[:24]:
            m.AiChatRequestReceipts.objects.filter(owner_email=row.session.owner_email, client_request_id="ding-"+row.id,
                status__in=["processing", "dispatched"]).update(status="unknown", error_code="ai_chat_result_unknown", completed_at=timezone.now())
        m.AiDingTalkReceipt.objects.filter(status__in=["running", "sending"]).update(
            status="unknown", error_code="interrupted_result_unknown", updated_at=timezone.now())
        m.AiDingTalkReceipt.objects.filter(ack_status="sending").update(ack_status="unknown")


def step(config_reader, sender):
    """One durable work item. Sender receives a trusted session and plain text."""
    with mutation():
        row = m.AiDingTalkReceipt.objects.filter(status="ready").order_by("created_at").first()
        if row is None:
            busy = m.AiDingTalkReceipt.objects.filter(status__in=["running", "sending"]).values("session_id")
            row = m.AiDingTalkReceipt.objects.filter(status="queued").exclude(session_id__in=busy).order_by("created_at").first()
            if row is None:
                return False
            row.status = "running"
        else:
            row.status = "sending"
        row.updated_at = timezone.now()
        row.save(update_fields=["status", "updated_at"])
    session = row.session
    try:
        principal = guard(session, config_reader())
        if row.status == "running":
            if row.ack_status == "pending":
                with mutation(principal):
                    row.ack_status = "sending"
                    row.save(update_fields=["ack_status"])
                try:
                    sender(session, "收到，正在查询系统数据；结果将在本群回复。" if session.conversation_type == "2" else "收到，正在查询系统数据；结果将在当前私聊回复。")
                except Exception:
                    with mutation():
                        row.ack_status = "unknown"
                        row.save(update_fields=["ack_status"])
                    raise AiError("钉钉发送通道不可用", "delivery_unknown", 503)
                with mutation(principal):
                    row.ack_status = "sent"
                    row.save(update_fields=["ack_status"])
            check = lambda: guard(session, config_reader())
            with transport.request_budget(260):
                answer = chat.answer({
                    "clientRequestId": "ding-" + row.id, "message": row.prompt,
                    "conversationId": session.conversation_id, "workspaceModule": "ai", "title": "志高助手 · 钉钉问数",
                }, principal, "ding-" + row.id, dingtalk_session=session, dingtalk_unbounded_total=True,
                    channel_guard=check, channel_time=row.created_at)
            check()
            with mutation(principal):
                row.reply = plain_reply(answer["reply"])
                row.prompt = ""  # Canonical user message is now in the AI conversation.
                row.status = "ready"
                row.save(update_fields=["reply", "prompt", "status"])
            return True
        # Sending was durably reserved before the external effect. No retry on ambiguity.
        sender(session, row.reply)
        guard(session, config_reader())
        mark(row, "sent")
    except AiError as error:
        if row.status == "running" and error.status != 403 and error.code != "delivery_unknown":
            with mutation():
                row.status, row.error_code = "ready", error.code
                row.reply = "这次分析未完成，未能取得可确认的完整结果。没有自动重复调用模型；请缩小日期、店铺或货品范围后重新提问。"
                row.save(update_fields=["status", "error_code", "reply"])
        else:
            mark(row, "unknown" if row.status == "sending" or error.code == "delivery_unknown" else "denied", error.code)
    except Exception:
        if row.status == "running" and row.ack_status == "sent":
            with mutation():
                row.status, row.error_code = "ready", "analysis_result_unavailable"
                row.reply = "本次分析结果未能确认，没有自动重复分析。请稍后缩小问题范围重新提问。"
                row.save(update_fields=["status", "error_code", "reply"])
        else:
            mark(row, "unknown" if row.status == "sending" else "failed", "channel_unavailable")
    return True

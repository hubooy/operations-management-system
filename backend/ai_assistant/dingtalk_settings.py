"""Administrator-owned group policy; application credentials stay in DWS."""
import json
from django.utils import timezone
from . import models as m
from .policy import AiError, canonical, current_principal, fields, mutation, text

IDENTITY = ("profile", "corpId", "unifiedAppId", "robotCode", "robotName")


def identity(config):
    return {key: config[key] for key in IDENTITY}


def groups(value):
    from .dingtalk import opaque
    if not isinstance(value, list) or len(value) > 30:
        raise AiError("最多配置 30 个 AI 对话群")
    seen = set()
    result = []
    for item in value:
        fields(item, {"id", "name", "enabled"}, {"id", "name", "enabled"})
        group_id = opaque(item["id"], "群 ID", 256)
        name = text(item["name"], "群名称", 100)
        if group_id in seen or type(item["enabled"]) is not bool:
            raise AiError("群 ID 重复或开关无效")
        seen.add(group_id)
        result.append({"id": group_id, "name": name, "enabled": item["enabled"]})
    return result


def initialize(config):
    """Explicit receiver startup adopts trusted runtime identity exactly once."""
    with mutation():
        row, _ = m.AiDingTalkSettings.objects.get_or_create(pk=1, defaults={
            "identity_json": canonical(identity(config)), "enabled": config["enabled"],
            "groups_json": canonical(groups([{**g, "enabled": True} for g in config["groups"]])),
            "updated_by": "dingtalk-runtime-initialization",
        })
        if row.identity_json != canonical(identity(config)):
            raise AiError("钉钉应用身份与已采用配置不一致", "access_denied", 403)


def effective(config):
    row = m.AiDingTalkSettings.objects.filter(pk=1).first()
    if not row or row.identity_json != canonical(identity(config)):
        raise AiError("钉钉 AI 配置尚未采用或应用身份已改变", "access_denied", 403)
    approved = groups(json.loads(row.groups_json))
    return {**config, "version": 2, "policyVersion": row.version,
        "enabled": config["enabled"] and row.enabled,
        "groups": [{"id": g["id"], "name": g["name"]} for g in approved if g["enabled"]]}


def read(principal):
    current_principal(principal, admin=True)
    row = m.AiDingTalkSettings.objects.filter(pk=1).first()
    if not row:
        return {"config": {"configured": False, "enabled": False, "groups": [], "version": 0,
            "robotName": "志高助手", "replyMode": "source", "canWrite": True}}
    return {"config": {"configured": True, "enabled": row.enabled, "groups": groups(json.loads(row.groups_json)),
        "version": row.version, "robotName": json.loads(row.identity_json)["robotName"],
        "replyMode": "source", "canWrite": True}}


def save(body, principal):
    current_principal(principal, admin=True)
    fields(body, {"enabled", "groups", "expectedVersion"}, {"enabled", "groups", "expectedVersion"})
    approved = groups(body["groups"])
    if type(body["enabled"]) is not bool or type(body["expectedVersion"]) is not int:
        raise AiError("开关或版本无效")
    with mutation(principal):
        row = m.AiDingTalkSettings.objects.select_for_update().filter(pk=1).first()
        if not row:
            raise AiError("请先由受控接收器采用应用身份", "not_configured", 409)
        if row.version != body["expectedVersion"]:
            raise AiError("配置已被修改，请重新加载后保存", "version_conflict", 409)
        row.enabled, row.groups_json = body["enabled"], canonical(approved)
        row.version += 1
        row.updated_by, row.updated_at = principal.email, timezone.now()
        row.save(update_fields=["enabled", "groups_json", "version", "updated_by", "updated_at"])
    return read(principal)

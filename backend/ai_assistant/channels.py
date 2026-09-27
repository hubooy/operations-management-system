import base64
import hashlib
import hmac
import re
import struct
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from django.utils import timezone
from . import models as m, transport
from .policy import (
    AiError,
    boolean,
    choice,
    current_principal,
    digest,
    fields,
    identifier,
    mutation,
    record,
    text,
    uid,
)
from .secrets import encrypt, decrypt

KINDS = [
    "dingtalk_group_bot",
    "dingtalk_app",
    "wechat_work_group_bot",
    "wechat_work_app",
]


def webhook(value):
    parts = urlsplit(text(value, "Webhook", 2000))
    if (
        parts.scheme != "https"
        or parts.username
        or parts.password
        or parts.fragment
        or parts.port not in {None, 443}
        or (parts.hostname, parts.path)
        not in {
            ("oapi.dingtalk.com", "/robot/send"),
            ("qyapi.weixin.qq.com", "/cgi-bin/webhook/send"),
        }
    ):
        raise AiError("Webhook 必须为受支持平台的精确 HTTPS 机器人地址")
    return value


def mapping(row):
    result = record(
        row,
        "id name kind status send_enabled callback_enabled receiver_id last_test_result last_tested_at created_at updated_at",
        bool_fields={"send_enabled", "callback_enabled"},
    )
    parts = urlsplit(row.webhook_url)
    result.update(
        targetDigest=target_digest(row),
        webhookUrlMasked=urlunsplit(
            (parts.scheme, parts.netloc, parts.path, "***" if parts.query else "", "")
        ),
        callbackTokenMasked="••••" + row.callback_token_suffix
        if row.callback_token_suffix
        else "",
        aesKeyMasked="••••" + row.aes_key_suffix if row.aes_key_suffix else "",
    )
    return result


def save(body, principal):
    current_principal(principal, write=True, admin=True)
    fields(
        body,
        {
            "id",
            "name",
            "kind",
            "status",
            "sendEnabled",
            "callbackEnabled",
            "webhookUrl",
            "callbackToken",
            "aesKey",
            "receiverId",
        },
        {"name", "kind"},
    )
    row = (
        m.AiChannels.objects.filter(id=identifier(body["id"])).first()
        if body.get("id")
        else None
    )
    if body.get("id") and not row:
        raise AiError("渠道不存在", "not_found", 404)
    row = row or m.AiChannels(id=uid("ai-channel"))
    row.name = text(body["name"], "name", 100)
    row.kind = choice(body["kind"], KINDS, "kind")
    row.status = choice(
        body.get("status", "enabled"), ["enabled", "disabled"], "status"
    )
    row.send_enabled = boolean(body.get("sendEnabled", False), "sendEnabled")
    row.callback_enabled = boolean(
        body.get("callbackEnabled", False), "callbackEnabled"
    )
    if body.get("webhookUrl"):
        row.webhook_url = webhook(body["webhookUrl"])
    for field, suffix in [("callbackToken", "callback_token"), ("aesKey", "aes_key")]:
        if body.get(field):
            raw = text(body[field], field, 2000)
            setattr(row, suffix + "_encrypted", encrypt(raw))
            setattr(row, suffix + "_suffix", raw[-4:])
    row.receiver_id = text(
        body.get("receiverId", row.receiver_id), "receiverId", 200, empty=True
    )
    if row.callback_enabled and (
        row.kind != "wechat_work_app"
        or not row.callback_token_encrypted
        or not row.aes_key_encrypted
        or not row.receiver_id
    ):
        raise AiError("回调仅支持已配置 Token、AES Key 和接收方的企业微信应用")
    if (row.send_enabled or row.kind.endswith("group_bot")) and not row.webhook_url:
        raise AiError("发送渠道需要 Webhook")
    row.updated_at = timezone.now()
    row.save()
    return {"item": mapping(row)}


def target_digest(row):
    return digest([row.id, row.kind, row.webhook_url, row.callback_token_encrypted, row.receiver_id, row.status, row.send_enabled])


def send(body, principal, *, expected_target_digest=None):
    current_principal(principal, write=True, admin=True)
    fields(body, {"id", "action", "text"}, {"id", "action"})
    action = choice(body["action"], ["test", "send"], "action")
    row = m.AiChannels.objects.filter(
        id=identifier(body["id"]), status="enabled", send_enabled=1
    ).first()
    if not row:
        raise AiError("发送渠道不可用", "not_found", 404)
    if expected_target_digest is not None and target_digest(row) != expected_target_digest:
        raise AiError("通知渠道配置已变化，禁止改投", "conflict", 409)
    content = (
        "AI 助理连接测试"
        if action == "test"
        else text(body.get("text"), "text", 4000)
    )
    target = webhook(row.webhook_url)
    if row.kind.startswith("dingtalk") and row.callback_token_encrypted:
        secret = decrypt(row.callback_token_encrypted)
        stamp = str(int(time.time() * 1000))
        signature = base64.b64encode(
            hmac.new(
                secret.encode(), (stamp + "\n" + secret).encode(), hashlib.sha256
            ).digest()
        ).decode()
        parts = urlsplit(target)
        query = dict(parse_qsl(parts.query))
        query.update(timestamp=stamp, sign=signature)
        target = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), "")
        )
    result = transport.bounded_json(
        target,
        {"msgtype": "text", "text": {"content": content}},
        timeout=15,
        maximum=256 * 1024,
    )
    if result.get("errcode") != 0:
        raise AiError("平台未确认发送成功", "channel_send_failed", 503)
    with mutation(principal):
        m.AiChannels.objects.filter(id=row.id).update(
            last_test_result="发送成功", last_tested_at=timezone.now()
        )
    return {"ok": True, "message": "发送成功"}


def xml_value(value, name):
    match = re.search(
        r"<"
        + re.escape(name)
        + r">(?:<!\[CDATA\[([\s\S]*?)\]\]>|([\s\S]*?))</"
        + re.escape(name)
        + r">",
        value,
        re.I,
    )
    return (match[1] or match[2] or "").strip() if match else ""


def callback(channel_id, body):
    fields(body, {"method", "query", "body"}, {"method", "query", "body"})
    row = m.AiChannels.objects.filter(
        id=identifier(channel_id),
        kind="wechat_work_app",
        status="enabled",
        callback_enabled=1,
    ).first()
    if not row:
        raise AiError("not found", "not_found", 404)
    query = body["query"]
    fields(
        query,
        {"echostr", "timestamp", "nonce", "msg_signature"},
        {"timestamp", "nonce", "msg_signature"},
    )
    raw = text(body["body"], "body", 65536, empty=True)
    if "<!DOCTYPE" in raw.upper() or "<!ENTITY" in raw.upper():
        raise AiError("invalid callback", "access_denied", 403)
    encrypted = (
        query.get("echostr", "")
        if body["method"] == "GET"
        else xml_value(raw, "Encrypt")
    )
    token = decrypt(row.callback_token_encrypted)
    expected = hashlib.sha1(
        "".join(sorted([token, query["timestamp"], query["nonce"], encrypted])).encode()
    ).hexdigest()
    if not hmac.compare_digest(expected, query["msg_signature"].lower()):
        raise AiError("invalid callback", "access_denied", 403)
    try:
        key = base64.b64decode(
            decrypt(row.aes_key_encrypted).rstrip("=") + "=", validate=True
        )
        ciphertext = base64.b64decode(encrypted, validate=True)
        decryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
        plain = decryptor.update(ciphertext) + decryptor.finalize()
        padding = plain[-1]
        if not 1 <= padding <= 32 or plain[-padding:] != bytes([padding]) * padding:
            raise ValueError()
        plain = plain[:-padding]
        length = struct.unpack(">I", plain[16:20])[0]
        end = 20 + length
        if end > len(plain) or plain[end:].decode() != row.receiver_id:
            raise ValueError()
        value = plain[20:end].decode()
    except (ValueError, IndexError, struct.error) as e:
        raise AiError("invalid callback", "access_denied", 403) from e
    if body["method"] == "GET":
        return {"text": value}
    event_key = (
        xml_value(value, "MsgId")
        or ":".join(
            filter(
                None,
                [
                    xml_value(value, "FromUserName"),
                    xml_value(value, "CreateTime"),
                    xml_value(value, "Event"),
                ],
            )
        )
        or digest(value)
    )
    previous = m.AiChannelCallbackEvents.objects.filter(
        channel_id=row.id, event_key=event_key
    ).first()
    if previous and previous.payload_digest != digest(value):
        raise AiError("回调事件标识冲突", "conflict", 409)
    if not previous:
        m.AiChannelCallbackEvents.objects.create(
            id=uid("ai-callback"),
            channel_id=row.id,
            event_key=event_key,
            payload_digest=digest(value),
        )
    return {"text": "success"}

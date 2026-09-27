"""Enterprise application delivery; no personal DWS authorization at runtime."""
import json
import html
import ipaddress
import secrets
import re
from urllib.parse import urlsplit
from .dingtalk import guard
from .policy import AiError

STREAM_API = "https://api.dingtalk.com/v1.0/gateway/connections/open"
STREAM_SOCKET = "https://wss-open-connection.dingtalk.com/connect"
TOKEN_API = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
GROUP_MEDIA_API = "https://api.dingtalk.com/v1.0/robot/groupMessages/send"
PERSON_MEDIA_API = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend"
GROUP_BOTS_API = "https://api.dingtalk.com/v1.0/robot/groups/robots/query"
UPLOAD_API = "https://oapi.dingtalk.com/media/upload"


def stream_addresses(url):
    from . import transport
    def official(value):
        if value not in (STREAM_API, STREAM_SOCKET):
            raise AiError("钉钉连接地址不在固定官方入口", "access_denied", 403)
    official(url)
    addresses = transport.resolve_addresses(urlsplit(url).hostname, 443, 5)
    addresses = transport._public_addresses(url, addresses, 5, official)
    if not addresses or len(addresses) > 32 or any(not ipaddress.ip_address(a[4][0]).is_global or ipaddress.ip_address(a[4][0]).is_multicast for a in addresses):
        raise AiError("钉钉地址未解析到公网", "access_denied", 403)
    return addresses


def media_addresses(url):
    """Pin only the exact DingTalk application endpoints to verified public DNS."""
    from . import transport
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.username or parts.password or parts.fragment or parts.port not in (None, 443)
            or (parts.hostname, parts.path) not in {
                ("api.dingtalk.com", "/v1.0/oauth2/accessToken"),
                ("api.dingtalk.com", "/v1.0/robot/groupMessages/send"),
                ("api.dingtalk.com", "/v1.0/robot/oToMessages/batchSend"),
                ("api.dingtalk.com", "/v1.0/robot/groups/robots/query"),
                ("oapi.dingtalk.com", "/media/upload"),
            } or (parts.query and parts.path != "/media/upload")):
        raise AiError("钉钉媒体地址不在固定官方入口", "access_denied", 403)
    addresses = transport.resolve_addresses(parts.hostname, 443, 5)
    addresses = transport._public_addresses(url, addresses, 5, lambda value: media_addresses_origin(value))
    if not addresses or len(addresses) > 32 or any(
            not ipaddress.ip_address(a[4][0]).is_global or ipaddress.ip_address(a[4][0]).is_multicast
            for a in addresses):
        raise AiError("钉钉媒体地址未解析到公网", "access_denied", 403)
    return addresses


def media_addresses_origin(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in {"api.dingtalk.com", "oapi.dingtalk.com"}:
        raise AiError("钉钉媒体地址不在固定官方入口", "access_denied", 403)


def image_markdown(caption, media_id):
    """Render a literal caption and exactly one internal DingTalk image."""
    if not re.fullmatch(r"@[A-Za-z0-9._~=-]{1,4095}", media_id):
        raise AiError("钉钉图片标识无效", "channel_unavailable", 503)
    literal = html.escape(caption, quote=False)
    literal = re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", literal)
    body = literal.replace("\n", "  \n") + "\n\n![完整截图](" + media_id + ")"
    if len(body) > 5000 or len(body.encode("utf-8")) > 20000:
        raise AiError("文案超出图文消息上限", "payload_too_large", 413)
    return {"title": caption.splitlines()[0][:100], "text": body}


def send_media(config_reader, session, raw, file_name, kind, caption="", *, before_send=None):
    """One bot-authored attachment. The caller reserves its run before this call."""
    from . import transport
    if not isinstance(caption, str) or len(caption) > 4000 or (caption and kind != "image"):
        raise AiError("图片附带文案无效")
    caption = caption.strip()
    if caption:
        image_markdown(caption, "@media")  # Reject oversize literal text before any network call.
    if kind not in {"image", "file"} or not isinstance(raw, bytes) or not 0 < len(raw) <= 2 * 1024 * 1024:
        raise AiError("钉钉媒体内容无效", "payload_too_large", 413)
    if not isinstance(file_name, str) or not 0 < len(file_name) <= 120 or any(c in file_name for c in "\\/:*?\"<>|\r\n"):
        raise AiError("钉钉文件名无效")
    extension = file_name.rsplit(".", 1)[-1].lower()
    if (kind == "image" and (extension != "png" or not raw.startswith(b"\x89PNG\r\n\x1a\n"))
            or kind == "file" and (extension not in {"xlsx", "pdf"}
                                   or extension == "xlsx" and not raw.startswith(b"PK\x03\x04")
                                   or extension == "pdf" and not raw.startswith(b"%PDF-"))):
        raise AiError("钉钉媒体格式无效")
    config = config_reader()
    guard(session, config)
    token = access_token(config)
    boundary = secrets.token_hex(16)
    name = file_name.encode("utf-8")
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"media\"; filename=\"".encode()
        + name + b"\"\r\nContent-Type: application/octet-stream\r\n\r\n" + raw
        + f"\r\n--{boundary}--\r\n".encode())
    upload_url = UPLOAD_API + "?access_token=" + token + "&type=" + kind
    uploaded = transport._bounded_json(upload_url, body,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
        timeout=30, maximum=4096, fixed_addresses=media_addresses(upload_url))
    media_id = uploaded.get("media_id")
    if uploaded.get("errcode") != 0 or not isinstance(media_id, str) or not media_id.startswith("@") or len(media_id) > 4096:
        raise AiError("钉钉媒体上传未确认", "channel_unavailable", 503)
    # Recheck the exact source and destination after upload, before external send.
    guard(session, config_reader())
    robot(config)
    if session.conversation_type == "2":
        group = next((g for g in config["groups"] if g["id"] == session.external_conversation_id), None)
        if not group:
            raise AiError("群聊已撤销授权", "access_denied", 403)
        verify_group(config, group, token)
        endpoint = GROUP_MEDIA_API
        target = {"openConversationId": session.external_conversation_id}
    else:
        endpoint = PERSON_MEDIA_API
        target = {"userIds": [session.sender_id]}
    params = {"photoURL": media_id} if kind == "image" else {
        "mediaId": media_id, "fileName": file_name, "fileType": extension}
    message_key = "sampleImageMsg" if kind == "image" else "sampleFile"
    if caption:
        params = image_markdown(caption, media_id)
        message_key = "sampleMarkdown"
    if before_send is not None:
        before_send()
    reply = transport._bounded_json(endpoint, {"robotCode": config["robotCode"],
        "msgKey": message_key,
        "msgParam": json.dumps(params, ensure_ascii=False, separators=(",", ":")), **target},
        headers={"x-acs-dingtalk-access-token": token},
        timeout=30, maximum=8192, fixed_addresses=media_addresses(endpoint))
    validate_receipt(reply)


def open_stream(key, secret):
    from . import transport
    return transport._bounded_json(STREAM_API,
        {"clientId": key, "clientSecret": secret, "ua": "teruisi-readonly/1", "localIp": "127.0.0.1",
         "subscriptions": [{"type": "CALLBACK", "topic": "/v1.0/im/bot/messages/get"}]},
        timeout=15, maximum=16384, fixed_addresses=stream_addresses(STREAM_API))


def credentials(config):
    from .dingtalk_bot_credentials import read_credentials
    return read_credentials(config)


def robot(config):
    # Identity was verified at provisioning and is bound inside the encrypted payload.
    # Every actual send authenticates this app against the official token endpoint.
    credentials(config)


def access_token(config):
    from . import transport
    key, secret = credentials(config)
    reply = transport._bounded_json(TOKEN_API, {"appKey": key, "appSecret": secret},
        timeout=15, maximum=4096, fixed_addresses=media_addresses(TOKEN_API))
    token = reply.get("accessToken")
    if not isinstance(token, str) or not 0 < len(token) <= 4096 or not re.fullmatch(r"[A-Za-z0-9._~-]+", token):
        raise AiError("企业机器人应用认证失败", "channel_unavailable", 503)
    return token


def verify_group(config, group, token=None):
    from . import transport
    if group not in config["groups"]:
        raise AiError("群聊已撤销授权", "access_denied", 403)
    headers = {"x-acs-dingtalk-access-token": token or access_token(config)}
    # The approved immutable openConversationId is the destination authority.
    # A mutable group display name is never resolved to a new recipient.
    reply = transport._bounded_json(GROUP_BOTS_API, {"openConversationId": group["id"]},
        headers=headers, timeout=15, maximum=65536, fixed_addresses=media_addresses(GROUP_BOTS_API))
    bots = reply.get("chatbotInstanceVOList")
    if not isinstance(bots, list) or len(bots) > 100 or any(not isinstance(bot, dict) for bot in bots):
        raise AiError("群机器人列表无法确认", "access_denied", 403)
    matches = [bot for bot in bots if bot.get("name") == config["robotName"]]
    if len(matches) != 1 or matches[0].get("robotCode") != config["robotCode"]:
        raise AiError("机器人未唯一安装到指定群", "access_denied", 403)


def send(config_reader, session, content, *, before_send=None):
    from . import transport
    config = config_reader()
    guard(session, config)
    if not isinstance(content, str) or not content or len(content.encode("utf-8")) > 20000:
        raise AiError("钉钉文字内容无效或过长", "payload_too_large", 413)
    token = access_token(config)
    if session.conversation_type == "2":
        group = next((g for g in config["groups"] if g["id"] == session.external_conversation_id), None)
        if not group:
            raise AiError("群聊已撤销授权", "access_denied", 403)
        verify_group(config, group, token)
        endpoint, target = GROUP_MEDIA_API, {"openConversationId": session.external_conversation_id}
    else:
        endpoint, target = PERSON_MEDIA_API, {"userIds": [session.sender_id]}
    guard(session, config_reader())
    robot(config)  # Re-read the credential binding immediately before external send.
    if before_send is not None:
        before_send()
    reply = transport._bounded_json(endpoint, {"robotCode": config["robotCode"], "msgKey": "sampleText",
        "msgParam": json.dumps({"content": content}, ensure_ascii=False, separators=(",", ":")), **target},
        headers={"x-acs-dingtalk-access-token": token}, timeout=30, maximum=8192,
        fixed_addresses=media_addresses(endpoint))
    validate_receipt(reply)


def validate_receipt(reply):
    if (not isinstance(reply.get("processQueryKey"), str) or not 0 < len(reply["processQueryKey"]) <= 4096
            or reply.get("invalidStaffIdList") not in (None, [])
            or reply.get("flowControlledStaffIdList") not in (None, [])):
        raise AiError("钉钉发送回执未确认", "delivery_unknown", 503)

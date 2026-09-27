"""CurrentUser DPAPI credentials, bound to the already approved application identity."""
import base64
import json
import os
from pathlib import Path

from pandas_runner.key_file import dpapi
from .dingtalk_settings import identity
from .policy import AiError, canonical


def checked_path(name, *, existing=True):
    path = Path(name)
    if not name or not path.is_absolute():
        raise ValueError("invalid_path")
    for item in (path, *path.parents):
        if item.is_symlink() or item.is_junction():
            raise ValueError("reparse_path")
    if existing:
        stat = path.stat()
        if not path.is_file() or stat.st_nlink != 1 or not 0 < stat.st_size <= 65536:
            raise ValueError("invalid_file")
    return path


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def read_credentials(config):
    try:
        if os.name != "nt":
            raise ValueError("windows_dpapi_required")
        path = checked_path(os.environ.get("TERUISI_DINGTALK_BOT_CREDENTIALS", ""))
        outer = json.loads(path.read_bytes(), object_pairs_hook=unique)
        if set(outer) != {"version", "payloadDpapiBase64"} or type(outer["version"]) is not int or outer["version"] != 1:
            raise ValueError("invalid_envelope")
        payload = json.loads(dpapi(base64.b64decode(outer["payloadDpapiBase64"], validate=True)), object_pairs_hook=unique)
        if (set(payload) != {"identity", "appKey", "appSecret"} or payload["identity"] != identity(config)
                or payload["appKey"] != config["robotCode"] or not isinstance(payload["appSecret"], str)
                or not 1 <= len(payload["appSecret"]) <= 4096):
            raise ValueError("identity_mismatch")
        return payload["appKey"], payload["appSecret"]
    except Exception:
        # Never fall back to personal DWS login or expose DPAPI/platform payloads.
        raise AiError("企业机器人凭据未配置、已损坏或身份不匹配", "bot_credentials_unavailable", 503) from None


def provision(config, destination):
    """Explicit operator-only, create-only adoption; no credential rotation or sending."""
    from . import dingtalk_provision
    if os.name != "nt":
        raise AiError("企业机器人凭据仅支持 Windows DPAPI")
    path = checked_path(destination, existing=False)
    if path.exists():
        raise AiError("企业机器人凭据已存在；拒绝覆盖")
    key, secret = dingtalk_provision.credentials(config)
    protected = dpapi(canonical({"identity": identity(config), "appKey": key, "appSecret": secret}).encode(), protect=True)
    envelope = canonical({"version": 1, "payloadDpapiBase64": base64.b64encode(protected).decode("ascii")}).encode()
    # Parent ACL is checked by the runtime operator. Exclusive create prevents races.
    with path.open("xb") as stream:
        stream.write(envelope)
        stream.flush()
        os.fsync(stream.fileno())

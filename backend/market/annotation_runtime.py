"""Bounded, persisted inference concurrency and channel-local retry policy."""
from __future__ import annotations

import math


FAILURE_MESSAGES = {
    "authorization_revoked": "任务创建人的账号或模型使用权限已变化，请检查权限后续跑",
    "model_configuration": "视觉模型配置不可用，请检查模型与渠道配置后续跑",
    "provider_rate_limit": "视觉模型服务限流，已降低并发并等待后重试",
    "model_timeout": "视觉模型请求超时，已降低并发并稍后重试",
    "model_network": "视觉模型连接或服务暂时异常，已降低并发并稍后重试",
    "model_response": "视觉模型返回格式无效，请检查模型与 Prompt",
    "image_fetch": "模型输入图片读取或处理失败，请检查图片",
    "annotation_failed": "图片识别失败，请检查模型配置后续跑",
    "inference_result_unknown": "推理租约已过期且结果未知，已暂停并隔离该项；请人工核验后处理下一批",
}


def _bounded(value: object, fallback: int, maximum: int, minimum: int = 0) -> int:
    return max(minimum, min(maximum, value)) if isinstance(value, int) and not isinstance(value, bool) else fallback


def retry_snapshot(raw: object, configured: int) -> dict:
    value = raw if isinstance(raw, dict) else {}
    old_target = _bounded(value.get("configuredConcurrency"), configured, 50, 1)
    current = _bounded(value.get("currentConcurrency"), configured, 50, 1)
    current = min(configured, current) if current < old_target else configured
    result = {
        "configuredConcurrency": configured,
        "currentConcurrency": current,
        "transientFailureCount": _bounded(value.get("transientFailureCount"), 0, 1000),
        "rateLimitFailureCount": _bounded(value.get("rateLimitFailureCount"), 0, 1000),
        "successfulImagesSinceFailure": _bounded(value.get("successfulImagesSinceFailure"), 0, 1_000_000),
        "transientIncidentUntil": _bounded(value.get("transientIncidentUntil"), 0, 10**15),
        "globalRateLimitUntil": _bounded(value.get("globalRateLimitUntil"), 0, 10**15),
        "floorFailureCount": _bounded(value.get("floorFailureCount"), 0, 3),
        "workerRetryUntil": [],
    }
    workers = value.get("workerRetryUntil", [])
    if isinstance(workers, list):
        for entry in workers[:50]:
            if isinstance(entry, list) and len(entry) == 2:
                lane = _bounded(entry[0], -1, 49)
                until = _bounded(entry[1], 0, 10**15)
                if lane >= 0 and until:
                    result["workerRetryUntil"].append([lane, until])
    return result


def blocked_until(retry: dict, lane: int) -> int:
    return max(retry["globalRateLimitUntil"], dict(retry["workerRetryUntil"]).get(lane, 0))


def record_failure(retry: dict, code: str, lane: int, now_ms: int, retry_after_ms: int = 0) -> bool:
    """Return whether this run must pause; repeated failures in a window count once."""
    if code in {"authorization_revoked", "model_configuration"}:
        return True
    if code not in {"provider_rate_limit", "model_timeout", "model_network"}:
        return False
    rate_limit = code == "provider_rate_limit"
    kind = "rateLimit" if rate_limit else "transient"
    previous = retry["currentConcurrency"]
    incident_until = retry["globalRateLimitUntil"] if rate_limit else max(retry["globalRateLimitUntil"], retry["transientIncidentUntil"])
    counted = now_ms >= incident_until
    count_key = kind + "FailureCount"
    if counted:
        retry[count_key] = min(1000, retry[count_key] + 1)
        if rate_limit:
            retry["currentConcurrency"] = max(1, previous // 2)
        elif retry[count_key] == 1:
            retry["currentConcurrency"] = max(2 if retry["configuredConcurrency"] >= 4 else 1, math.ceil(previous * .75))
        else:
            retry["currentConcurrency"] = max(1, math.ceil(previous / 2))
        retry["successfulImagesSinceFailure"] = 0
        retry["floorFailureCount"] = min(3, retry["floorFailureCount"] + 1) if previous == 1 else 0
    delay = min(300_000 if rate_limit else 30_000, (60_000 if rate_limit else 5_000) * 2 ** min(4, max(0, retry[count_key] - 1)))
    if rate_limit:
        delay = min(300_000, max(delay, retry_after_ms))
        # The original incident fixes the window. Concurrent failures do not extend it indefinitely.
        if counted:
            retry["globalRateLimitUntil"] = now_ms + delay
    else:
        workers = dict(retry["workerRetryUntil"])
        workers[lane] = now_ms + delay
        retry["workerRetryUntil"] = [list(entry) for entry in sorted(workers.items())][:50]
        if counted:
            retry["transientIncidentUntil"] = now_ms + delay
    return retry["floorFailureCount"] >= 3


def record_success(retry: dict) -> None:
    retry["floorFailureCount"] = 0
    retry["successfulImagesSinceFailure"] += 1
    if retry["successfulImagesSinceFailure"] >= 3 and retry["currentConcurrency"] < retry["configuredConcurrency"]:
        retry["currentConcurrency"] += 1
        retry["successfulImagesSinceFailure"] -= 3
        retry["transientFailureCount"] = 0
        retry["rateLimitFailureCount"] = 0

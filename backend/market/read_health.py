"""Passive business health: no scans, user IDs, query text or business rows."""
from copy import deepcopy
from threading import Lock
from time import monotonic, time

from django.http import JsonResponse
from django.views.decorators.http import require_GET

_lock = Lock()
_reads = {}


def record(operation, started, code="ok"):
    if not isinstance(operation, str) or operation not in {"filter_options", "ranking"}:
        return
    with _lock:
        previous = _reads.get(operation, {})
        if started < previous.get("started", 0):
            return
        _reads[operation] = {
            "started": started,
            "observedAt": int(time()), "durationMs": round((monotonic() - started) * 1000),
            "code": code,
            "consecutiveFailures": 0 if code == "ok" else previous.get("consecutiveFailures", 0) + 1,
        }


@require_GET
def endpoint(request):
    if request.META.get("REMOTE_ADDR") not in {"127.0.0.1", "::1"}:
        return JsonResponse({"error": "local_only"}, status=403)
    now = int(time())
    with _lock:
        reads = deepcopy(_reads)
    for row in reads.values():
        row.pop("started", None)
    recent = [r for r in reads.values() if 0 <= now - r["observedAt"] <= 900]
    degraded = any(r["consecutiveFailures"] >= 2 for r in recent)
    status = "degraded" if degraded else "observing" if any(r["code"] != "ok" for r in recent) else "healthy" if len(recent) == 2 else "unknown"
    response = JsonResponse({"version": "market-read-health-v1", "status": status, "reads": reads})
    response["Cache-Control"] = "no-store"
    return response

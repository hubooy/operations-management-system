"""Exact, unregistered transport contract for the paused market-v2 fifth tool.

This only sizes an owning read for a future central registry entry.  It grants
no model call, dispatch, persisted receipt or report publication.
"""
import re
import time

from business_analysis.contracts import AnalysisContractError, canonical, digest
from business_analysis.promotion_views import _copy
from . import business_market_v2_fifth_read_contract as fifth
from . import business_promotion_market_runtime_v2_contract as runtime


SCHEMA = "business-market-v2-transport-candidate-v1"
RESULT_SCHEMA = "business-market-v2-tool-result-candidate-v1"
SURFACE = "business_agent_screening_promotion_market_v2"
PROFILE = "business-agent-screening-promotion-market-admitted-v2"
TOOL = runtime.MARKET_TOOL
MAX_RESULT_CHARACTERS = 38_000
MAX_RESULT_BYTES = 38_000
TIMEOUT_MS = 12_000
MAX_CALLS_PER_REQUEST = 8
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_COMMON = {"reportId", "marketContextDigest", "mode"}
_PAGE = _COMMON | {"view", "offset", "limit"}
_ROW = _COMMON | {"view", "rowIndex", "rowId"}
RESULT_FIELDS = {"schemaVersion", "surface", "profile", "toolName",
    "reportId", "sourceReportId", "role", "mode", "identityClaimDigest",
    "jobIdClaim", "providerDispatchIdClaim", "providerCallIdClaim",
    "marketManifestDigest", "payload", "citationBases", "sourceResultDigest",
    "numericReferenceRequiredFields", "serverFullMarketMaterialVerified",
    "sameJobProviderPersisted", "persistedRead", "registeredTool",
    "authorityVerified", "resultDigest"}


def _need(ok, message="市场v2第五工具候选参数或容量无效"):
    if not ok:
        raise AnalysisContractError(message)


def definition():
    """Reviewable candidate, deliberately not a central registry entry."""
    token = {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,160}$"}
    sha = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
    common = {"reportId": token, "marketContextDigest": sha}
    variants = []
    for mode, extra in (("summary", {}), ("page", {
            "view": {"type": "string", "enum": list(runtime.TOOL_VIEWS)},
            "offset": {"type": "integer", "minimum": 0,
                "maximum": runtime.MAX_ROWS},
            "limit": {"type": "integer", "enum": [runtime.PAGE_SIZE]}}),
            ("row", {"view": {"type": "string", "enum": list(runtime.TOOL_VIEWS)},
            "rowIndex": {"type": "integer", "minimum": 0,
                "maximum": runtime.MAX_ROWS-1}, "rowId": sha})):
        variants.append({"type": "object", "properties": {**common,
            "mode": {"type": "string", "enum": [mode]}, **extra},
            "required": ["reportId", "marketContextDigest", "mode", *extra],
            "additionalProperties": False})
    value = {"schemaVersion": SCHEMA, "name": TOOL,
        "surface": SURFACE, "profile": PROFILE,
        "inputSchema": {"type": "object", "oneOf": variants},
        "allowedRoles": ["admin"], "scopePolicy": "unscoped_only",
        "rolePolicy": {"marketAllowed": sorted(runtime.MARKET_ROLES),
            "marketDenied": ["commerce", "promotion"]},
        "execution": {"environment": "worker_inline", "mode": "direct",
            "allowedSurfaces": [SURFACE], "timeoutMs": TIMEOUT_MS,
            "maxResultCharacters": MAX_RESULT_CHARACTERS,
            "maxCallsPerRequest": MAX_CALLS_PER_REQUEST},
        "registered": False, "persistedRead": False}
    value["definitionDigest"] = digest(value)
    return value


def request(surface, profile, tool, raw_call, raw_arguments):
    """Constrain model arguments separately from an injected identity claim."""
    _need((surface, profile, tool) == (SURFACE, PROFILE, TOOL))
    call = fifth.injected(raw_call)
    arguments = _copy(raw_arguments, 4096)
    _need(type(arguments) is dict
        and arguments.get("reportId") == call["admittedReportId"]
        and arguments.get("marketContextDigest") == call["marketContextDigest"]
        and type(arguments.get("reportId")) is str
        and _ID.fullmatch(arguments["reportId"]) is not None
        and type(arguments.get("marketContextDigest")) is str
        and _SHA.fullmatch(arguments["marketContextDigest"]) is not None)
    mode = arguments.get("mode")
    if mode == "summary":
        _need(set(arguments) == _COMMON)
    elif mode == "page":
        _need(set(arguments) == _PAGE
            and arguments["view"] in runtime.TOOL_VIEWS
            and type(arguments["offset"]) is int
            and 0 <= arguments["offset"] <= runtime.MAX_ROWS
            and type(arguments["limit"]) is int
            and arguments["limit"] == runtime.PAGE_SIZE)
    elif mode == "row":
        _need(set(arguments) == _ROW
            and arguments["view"] in runtime.TOOL_VIEWS
            and type(arguments["rowIndex"]) is int
            and 0 <= arguments["rowIndex"] < runtime.MAX_ROWS
            and type(arguments["rowId"]) is str
            and _SHA.fullmatch(arguments["rowId"]) is not None)
    else:
        _need(False)
    _need(len(canonical(arguments).encode("utf-8")) <= 4096)
    return call, arguments


def result(value):
    """Fail the complete output if either provider limit could be exceeded."""
    _need(type(value) is dict and set(value) == RESULT_FIELDS
        and value.get("schemaVersion") == RESULT_SCHEMA
        and value.get("surface") == SURFACE and value.get("toolName") == TOOL
        and value.get("profile") == PROFILE
        and value.get("role") in runtime.MARKET_ROLES
        and value.get("mode") in {"summary", "page", "row"}
        and value.get("numericReferenceRequiredFields") == ["metric", "field"]
        and value.get("serverFullMarketMaterialVerified") is True
        and value.get("sameJobProviderPersisted") is False
        and value.get("persistedRead") is False
        and value.get("registeredTool") is False
        and value.get("authorityVerified") is False)
    try:
        expected = digest({key: item for key, item in value.items()
            if key != "resultDigest"})
        raw = canonical(value)
    except (ValueError, TypeError, UnicodeError) as error:
        raise AnalysisContractError("市场v2第五工具候选结果不是有限规范JSON") from error
    _need(value["resultDigest"] == expected)
    _need(len(raw.encode("utf-8")) <= MAX_RESULT_BYTES
        and len(raw.encode("utf-16-le")) // 2 <= MAX_RESULT_CHARACTERS,
        "市场v2第五工具候选结果超过现有模型工具容量，整次拒绝")
    return value


class Deadline:
    """Cooperative wall-clock fence; a future registered tool also needs outer timeout."""
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._start = clock()

    def check(self):
        elapsed = (self._clock() - self._start) * 1000
        _need(type(elapsed) is float or type(elapsed) is int)
        _need(0 <= elapsed < TIMEOUT_MS,
            "市场v2第五工具读取达到12秒边界，整次拒绝")
        return elapsed

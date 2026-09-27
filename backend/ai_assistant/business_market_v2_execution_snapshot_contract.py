"""Pure frozen market-v2 five-tool execution snapshot, still paused."""
import re

from business_analysis.contracts import AnalysisContractError, canonical, digest
from business_analysis.promotion_views import _copy
from . import business_promotion_market_runtime_v2_contract as previous


PROFILE = "business-agent-screening-promotion-market-execution-v2"
SNAPSHOT_SCHEMA = "business-market-v2-execution-snapshot-v1"
INPUT_SCHEMA = "business-market-v2-execution-input-v1"
SURFACE = "business_agent_screening_promotion_market_v2"
PAUSE_REASON = "market_v2_execution_not_activated"
TOOL_ORDER = ("get_business_market_v2_screening_package",
    "get_business_market_v2_screening_analysis",
    "get_business_market_v2_screening_budget",
    "get_business_market_v2_keyword_sku",
    "get_business_promotion_market_v2")
BASE_TOOLS = previous.previous.TOOL_ORDER
CATALOG_DIGEST = "3db26b536d59118656f3a5f52638275139b19df24e2afcbf99b8c76cab8ee743"
GRAPH_DIGESTS = {False: "6a0b655cec19328b1c1e02e78330320e04f699b5037868faf42e2ac8f6b0f40d",
    True: "905ae3d59bd4e9554b22c7373274d0569aa2c9e5da7182338351fbf20623f1c2"}
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
ROOT_FIELDS = {"admittedReportId", "parkedReportId", "sourceReportId",
    "ownerEmail", "selectorDigest", "manifestDigest", "marketContextDigest",
    "withBudget"}


def _need(ok, message="市场v2执行快照候选不属于固定材料与五工具目录"):
    if not ok:
        raise AnalysisContractError(message)


def graph(with_budget):
    _need(type(with_budget) is bool)
    value = previous.graph(with_budget)
    for node in value["nodes"]:
        for old, new in zip(BASE_TOOLS, TOOL_ORDER[:4]):
            node["instruction"] = node["instruction"].replace(old, new)
    _need(digest(value) == GRAPH_DIGESTS[with_budget],
        "市场v2五工具图与冻结版本不同")
    return value


def catalog(entries):
    value = _copy(entries, 128*1024)
    _need(type(value) is list and len(value) == 5
        and tuple(entry.get("name") if type(entry) is dict else None
            for entry in value) == TOOL_ORDER
        and digest(value) == CATALOG_DIGEST,
        "当前中央新surface五工具目录与冻结摘要不同")
    for index, entry in enumerate(value):
        execution = entry["execution"]
        _need(entry["risk"] == "read_only"
            and entry["allowedRoles"] == ["admin"]
            and entry["scopePolicy"] == "unscoped_only"
            and entry["annotations"] == {"readOnlyHint": True,
                "destructiveHint": False, "idempotentHint": True,
                "openWorldHint": False}
            and execution["environment"] == "worker_inline"
            and execution["mode"] == "direct"
            and execution["allowedSurfaces"] == [SURFACE]
            and execution["timeoutMs"] == 12000
            and execution["maxResultCharacters"] ==
                (40000 if index < 3 else 38000)
            and execution["maxCallsPerRequest"] == 8)
    return value


def root(raw):
    value = _copy(raw, 4096)
    _need(type(value) is dict and set(value) == ROOT_FIELDS)
    for key in ("admittedReportId", "parkedReportId", "sourceReportId"):
        _need(type(value[key]) is str and _ID.fullmatch(value[key]) is not None)
    _need(len({value["admittedReportId"], value["parkedReportId"],
        value["sourceReportId"]}) == 3)
    _need(type(value["ownerEmail"]) is str and
        value["ownerEmail"] == value["ownerEmail"].lower()
        and "@" in value["ownerEmail"] and len(value["ownerEmail"]) <= 320)
    for key in ("selectorDigest", "manifestDigest", "marketContextDigest"):
        _need(type(value[key]) is str and _SHA.fullmatch(value[key]) is not None)
    _need(type(value["withBudget"]) is bool)
    return value


def build(report_id, raw_root, entries):
    """Return detached immutable report/flow input and graph, no DB writes."""
    _need(type(report_id) is str and _ID.fullmatch(report_id) is not None)
    fixed = root(raw_root)
    _need(report_id not in {fixed["admittedReportId"],
        fixed["parkedReportId"], fixed["sourceReportId"]})
    catalog(entries)
    fixed_graph = graph(fixed["withBudget"])
    common = {"executionProfile": PROFILE, "reportId": report_id,
        "executionRoot": fixed, "catalogSurface": SURFACE,
        "toolCatalogDigest": CATALOG_DIGEST, "proposedTools": list(TOOL_ORDER),
        "withBudget": fixed["withBudget"], "registeredCatalog": True,
        "runtimeActivated": False, "agentDispatchSupported": False,
        "agentReadPersisted": False, "humanReviewRequired": True,
        "marketAndOwnSalesAdditive": False}
    snapshot = {"schemaVersion": SNAPSHOT_SCHEMA, **common}
    workflow_input = {"schemaVersion": INPUT_SCHEMA, **common,
        "graphDigest": GRAPH_DIGESTS[fixed["withBudget"]],
        "allowedTools": list(TOOL_ORDER)}
    result = {"schemaVersion": "business-market-v2-execution-build-v1",
        "snapshot": snapshot, "workflowInput": workflow_input,
        "graph": fixed_graph, "catalogDigest": CATALOG_DIGEST,
        "status": "paused", "agentDispatchSupported": False,
        "authorityVerified": False}
    result["buildDigest"] = digest(result)
    _need(len(canonical(result).encode("utf-8")) <= 128*1024)
    return result

"""Pure candidate market-number references; never an Agent read authority.

A signed HTTP request proves the current principal at the reader edge, but
does not prove that a particular Agent consumed its response. The future v2
dispatch ledger must issue and verify the per-job read receipt before use.
"""
import re

from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, digest
from .market_dynamics import METRICS
from .promotion_views import _copy


SCHEMA = "business-market-numeric-reference-candidate-v1"
RECEIPT_SCHEMA = "business-market-agent-read-candidate-v1"
ROLES = frozenset(("market_b2b", "independent_review", "report"))
PRICE_FIELDS = {"jobId", "role", "reportId", "sourceKey", "view", "bandsDigest",
    "bandKey", "tableBindingDigest", "rowIndex", "rowId", "metric", "field", "attribution"}
RANK_FIELDS = {"jobId", "role", "reportId", "sourceKey", "view", "baselineKey",
    "currentObservationDate", "baselineObservationDate", "tableBindingDigest",
    "rowIndex", "rowId", "metric", "field", "attribution"}
RECEIPT_EXTRA = {"schemaVersion", "ownerEmail", "bindingDigest", "responseDigest",
    "signedRequestDigest"}
ATTRIBUTION = "market_top_sample_only"
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _need(ok, message="市场数值引用候选无效"):
    if not ok:
        raise AnalysisContractError(message)


def _id(value):
    _need(type(value) is str and _ID.fullmatch(value) is not None)


def _sha(value):
    _need(type(value) is str and _SHA.fullmatch(value) is not None)


def reference(raw):
    """Reject own-sales aliases, caller numbers and mixed selector modes."""
    value = _copy(raw, 4096)
    _need(type(value) is dict and value.get("view") in ("price_band", "rank_entry_exit"))
    _need(set(value) == (PRICE_FIELDS if value["view"] == "price_band" else RANK_FIELDS))
    for key in ("jobId", "reportId", "sourceKey"):
        _id(value[key])
    _need(value["role"] in ROLES and value["attribution"] == ATTRIBUTION)
    for key in ("tableBindingDigest", "rowId"):
        _sha(value[key])
    _need(type(value["rowIndex"]) is int and 0 <= value["rowIndex"] < 200_000)
    if value["view"] == "price_band":
        _sha(value["bandsDigest"])
        _need(type(value["bandKey"]) is str and 0 < len(value["bandKey"]) <= 100)
        _need(value["metric"] in METRICS and value["field"] in
            ("value", "presentRows", "missingRows"))
    else:
        _id(value["baselineKey"])
        _need(value["baselineKey"] != value["sourceKey"])
        from .contracts import strict_date
        strict_date(value["currentObservationDate"])
        strict_date(value["baselineObservationDate"])
        _need((value["metric"], value["field"]) in
            {("rank", "current"), ("rank", "baseline"), ("rankImprovement", "value"),
             ("sampleGmvLowerCents", "current"), ("sampleGmvUpperCents", "current"),
             ("sampleGmvLowerCents", "baseline"), ("sampleGmvUpperCents", "baseline")})
    return value


def bind_read(raw_reference, raw_receipt, *, job_id, role, report_id, owner_email):
    """Shape-and-equality gate only; no caller JSON can issue a read grant."""
    ref = reference(raw_reference)
    receipt = _copy(raw_receipt, 8192)
    _need(type(receipt) is dict and set(receipt) == set(ref) | RECEIPT_EXTRA
        and receipt.get("schemaVersion") == RECEIPT_SCHEMA)
    _id(job_id); _id(report_id)
    _need(role in ROLES and type(owner_email) is str
        and owner_email == owner_email.strip().lower() and "@" in owner_email
        and 1 <= len(owner_email) <= 320)
    _need((ref["jobId"], ref["role"], ref["reportId"]) == (job_id, role, report_id)
        and receipt["ownerEmail"] == owner_email)
    _need(all(receipt[key] == value for key, value in ref.items()),
        "市场行读取回执来自其他Agent、角色、报告或选择")
    for key in ("bindingDigest", "responseDigest", "signedRequestDigest"):
        _sha(receipt[key])
    value = {"schemaVersion": SCHEMA, "reference": ref,
        "readReceiptDigest": digest(receipt), "jobId": job_id, "role": role,
        "reportId": report_id, "ownerEmail": owner_email,
        "attribution": ATTRIBUTION,
        "verification": {"referenceShapeVerified": True,
            "sameJobCandidateMatched": True, "signedTransportVerified": False,
            "agentReadPersisted": False, "authorityVerified": False},
        "limitations": ["候选回执不是持久Agent派发或本人阅读证明。",
            "市场TOP样本金额不能加到本店、ERP、B端销售或利润。",
            "未观察到商品与缺少观察日均不能作零销量。"]}
    value["candidateDigest"] = digest(value)
    return value


def number(ref, row, *, observation_coverage=None):
    """Extract only an available numeric cell from an owning recomputed row."""
    ref = reference(ref)
    _need(type(row) is dict and row.get("rowIndex") == ref["rowIndex"]
        and row.get("rowId") == ref["rowId"])
    if ref["view"] == "price_band":
        _need(row.get("bandKey") == ref["bandKey"])
        metrics = row.get("metrics")
        _need(type(metrics) is dict and type(metrics.get(ref["metric"])) is dict)
        cell = metrics[ref["metric"]]
        _need(set(cell) == {"value", "presentRows", "missingRows"}
            and type(cell["presentRows"]) is int and cell["presentRows"] >= 0
            and type(cell["missingRows"]) is int and cell["missingRows"] >= 0
            and (cell["value"] is None or type(cell["value"]) is int))
        amount = cell[ref["field"]]
        partial = cell["missingRows"] > 0
    else:
        _need(type(observation_coverage) is dict and set(observation_coverage) ==
            {"currentDatePresent", "baselineDatePresent", "bothDatesPresent"}
            and all(type(item) is bool for item in observation_coverage.values())
            and observation_coverage["bothDatesPresent"] ==
                (observation_coverage["currentDatePresent"] and
                 observation_coverage["baselineDatePresent"]))
        _need(row.get("status") in {"both_observed", "entered_observed_top_sample",
            "left_observed_top_sample", "insufficient_date_coverage"})
        _need((row["status"] == "insufficient_date_coverage") ==
            (not observation_coverage["bothDatesPresent"]))
        for side in ("current", "baseline"):
            value = row.get(side)
            _need(type(value) is dict and value.get("status") in
                ({"observed", "not_observed_in_top_sample"}
                    if observation_coverage[side+"DatePresent"] else {"date_not_covered"}))
        if ref["metric"] == "rankImprovement":
            current, baseline = row.get("current"), row.get("baseline")
            _need(observation_coverage["bothDatesPresent"] and
                row["status"] == "both_observed" and type(current) is dict
                and type(baseline) is dict and current.get("status") ==
                baseline.get("status") == "observed"
                and type(current.get("rank")) is int and type(baseline.get("rank")) is int)
            amount = row.get("rankImprovement")
            _need(amount == baseline["rank"] - current["rank"])
        else:
            side = ref["field"]
            value = row.get(side)
            _need(observation_coverage[side+"DatePresent"] and type(value) is dict
                and value.get("status") == "observed")
            amount = (value.get("rank") if ref["metric"] == "rank" else
                (value.get("metrics") or {}).get(ref["metric"]))
        partial = False
    _need(type(amount) is int and -MAX_SAFE_INTEGER <= amount <= MAX_SAFE_INTEGER,
        "市场数值缺失或非整数，不能把空值解释为零")
    return {"value": amount, "partial": partial,
        "population": ATTRIBUTION, "ownSalesAttributionVerified": False}

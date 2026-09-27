"""Pure compact seal contract for a mixed, non-atomic v3 source directory."""
from __future__ import annotations

from datetime import timedelta
import re

from .contracts import AnalysisContractError, canonical, comparison_periods, digest, strict_date

SCHEMA = "business-evidence-seal-v3"
MAX_BYTES = 38_000
DOMAINS = frozenset(("sales", "netshop", "market", "finance"))
MEANING = "来源在不同时刻独立读取；封存只证明各来源的已审计页面与持久检查点，不能解释为跨来源原子快照或上游数字签名。"


def _require(condition, message):
    if not condition:
        raise AnalysisContractError(message)


def date_observations(query, pages, *, snapshot=False):
    """Expose exact dates with rows; days without rows must never become zero."""
    period = comparison_periods(query["startDate"], query["endDate"])[query.get("window", "current")]
    first = strict_date(period["startDate"])
    last = strict_date(period["endDate"])
    days = period["days"]
    if snapshot:
        return {"kind": "current_master_snapshot", "startDate": first.isoformat(),
                "endDate": last.isoformat(), "presentBits": None,
                "missingRowDateCount": None, "observedRowCutoffDate": None,
                "meaning": "当前主数据不是逐日事实，不推断缺日或历史库存。"}
    observed = set()
    for page in pages:
        for item in page["items"]:
            value = item.get("date")
            if value is not None:
                day = strict_date(value)
                _require(first <= day <= last, "封存行日期超出来源窗口")
                observed.add(day)
    bits = "".join("1" if first + timedelta(days=index) in observed else "0" for index in range(days))
    return {"kind": "observed_row_dates", "startDate": first.isoformat(),
            "endDate": last.isoformat(), "presentBits": bits,
            "missingRowDateCount": bits.count("0"),
            "observedRowCutoffDate": max(observed).isoformat() if observed else None,
            "meaning": "位图1仅表示该日有来源行；0表示未观察到行，不能解释为零销售、零投放或来源已最终结算。"}


def finance_months(query, complete):
    """Preserve natural-month publication and missing months without prorating."""
    months = query["months"]
    publication = complete["publication"]
    published = {item["month"] for item in publication["months"]}
    missing = [month for month in months if month not in published]
    _require(publication["missingMonths"] == missing, "财报封存缺月与拥有方发布清单不一致")
    return {"kind": "natural_month_publication", "months": months,
            "publishedBits": "".join("1" if month in published else "0" for month in months),
            "missingMonths": missing, "publicationDigest": digest(publication),
            "detailedCoverageDigest": digest(complete["coverage"]),
            "meaning": "仅标记真实完成批次；缺月、缺主体与空值不作零值，不将自然月财报按日摊入分析窗口。"}


def make(*, run_id, evidence_version, plan_digest, catalog_digest, sources, stored_bytes):
    _require(type(run_id) is str and run_id and type(evidence_version) is int and evidence_version >= 2,
             "封存任务身份或版本无效")
    _require(type(sources) is list and 2 <= len(sources) <= 48, "封存来源数量无效")
    _require(type(stored_bytes) is int and 0 < stored_bytes <= 64 * 1024 * 1024 - MAX_BYTES,
             "封存事实字节无效")
    keys, positions = set(), set()
    for proof in sources:
        _require(type(proof) is dict and set(proof) == {"sourceKey", "ordinal", "domain", "queryDigest",
            "sourceVersion", "checkpointDigest", "pageCount", "rowCount", "storedBytes",
            "sourceRef", "sourceRevision", "receiptCount", "receiptChainDigest", "coverage"},
            "封存来源字段不完整")
        _require(type(proof["sourceKey"]) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", proof["sourceKey"])
                 and type(proof["ordinal"]) is int and proof["ordinal"] >= 1
                 and type(proof["domain"]) is str and proof["domain"] in DOMAINS,
                 "封存来源字段类型无效")
        _require(proof["sourceKey"] not in keys and proof["ordinal"] not in positions
                 and proof["ordinal"] == len(positions) + 1,
                 "封存来源顺序或身份无效")
        _require(type(proof["pageCount"]) is int and proof["pageCount"] >= 1
                 and type(proof["receiptCount"]) is int and proof["receiptCount"] == proof["pageCount"]
                 and type(proof["sourceVersion"]) is int
                 and proof["sourceVersion"] == proof["pageCount"] + 1
                 and type(proof["rowCount"]) is int and proof["rowCount"] >= 0
                 and type(proof["storedBytes"]) is int and proof["storedBytes"] >= 1,
                 "封存来源事实或收据计数无效")
        for field in ("queryDigest", "checkpointDigest", "sourceRef", "receiptChainDigest"):
            _require(type(proof[field]) is str and len(proof[field]) == 64
                     and all(letter in "0123456789abcdef" for letter in proof[field]),
                     "封存来源摘要无效")
        _require(type(proof["sourceRevision"]) is str and 1 <= len(proof["sourceRevision"]) <= 128,
                 "封存来源修订无效")
        _require(type(proof["coverage"]) is dict and type(proof["coverage"].get("kind")) is str
                 and proof["coverage"]["kind"] in
                 ({"natural_month_publication"} if proof["domain"] == "finance" else
                  {"observed_row_dates", "current_master_snapshot"}), "封存覆盖口径无效")
        keys.add(proof["sourceKey"]); positions.add(proof["ordinal"])
    _require(sum(item["storedBytes"] for item in sources) == stored_bytes, "封存来源总字节不一致")
    for value in (plan_digest, catalog_digest):
        _require(type(value) is str and len(value) == 64
                 and all(letter in "0123456789abcdef" for letter in value), "封存计划摘要无效")
    base = {"schemaVersion": SCHEMA, "runId": run_id, "evidenceVersion": evidence_version,
        "planDigest": plan_digest, "catalogDigest": catalog_digest,
        "sources": sources, "sourcesDigest": digest(sources),
        "pageCount": sum(item["pageCount"] for item in sources),
        "rowCount": sum(item["rowCount"] for item in sources),
        "storedBytes": stored_bytes, "snapshotMeaning": MEANING,
        "receiptBound": True, "ownedReplayVerified": True,
        "sourceAuthorityVerified": False, "persistentEvidenceVerified": False,
        "reportGenerationSupported": False}
    result = {**base, "sealedDigest": digest(base)}
    _require(len(canonical(result).encode("utf-8")) <= MAX_BYTES, "封存收据目录超出预留容量")
    return result

"""Read immutable sealed facts with domain identity and complete-chain checks.

No source I/O or writes. A pages() traversal is certified only when exhausted;
callers must not publish a partially consumed traversal as complete evidence.
"""
from copy import deepcopy
from datetime import date
import json
from types import SimpleNamespace

from business_analysis.contracts import AnalysisContractError, PageReconciler, comparison_periods
from . import business_evidence_store as store, models as m
from .policy import AiError, authorize_owner, canonical, current_principal, digest


METADATA_FIELDS = ("coverage", "excludedOverlappingPeriodRows", "identityCheck", "availableDates", "metricSemantics")


def _require(condition):
    if not condition:
        raise AnalysisContractError("封存来源身份、范围或完整性不一致")


def _same(left, right):
    return canonical(left) == canonical(right)


class Reader:
    def __init__(self, evidence, principal):
        current_principal(principal, admin=True)
        authorize_owner(evidence, principal)
        if evidence.status != "sealed":
            raise AiError("读取完整证据需要已封存任务", "conflict", 409)
        # Copy scalar bindings: a caller mutating its ORM object cannot change
        # the version or JSON against which this reader verifies its pages.
        self._row = SimpleNamespace(**{key: getattr(evidence, key) for key in
            ("id", "version", "status", "plan_json", "state_json", "stored_bytes")})
        self._row.pk = self._row.id
        self._infos, self._bytes = {}, {}
        try:
            self._v2 = store.is_v2(self._row)
            plan = json.loads(self._row.plan_json)
            self._limit = 100 if plan.get("collector") else 10
            if plan.get("collector"):
                _require(plan["collector"] == {"version": 1, "surface": "business_collection", "pageSize": 100})
            if self._v2:
                store.verify_seal(self._row)
                self._sources = store.catalog(self._row)
            else:
                self._sources = plan["sources"]
                self._legacy_state = json.loads(self._row.state_json)
                _require(set(self._legacy_state) == {s["key"] for s in self._sources})
            _require(isinstance(self._sources, list) and 1 <= len(self._sources) <= (48 if self._v2 else 12))
            _require(len({s["key"] for s in self._sources}) == len(self._sources))
            self._by_key = {s["key"]: s for s in self._sources}
            store.assert_current(self._row)
        except (AnalysisContractError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
            raise AiError("封存目录未通过核验", "conflict", 409) from error

    @property
    def sources(self):
        return deepcopy(self._sources)

    def info(self, key):
        if not isinstance(key, str) or key not in self._by_key:
            raise AiError("来源不存在", "not_found", 404)
        if key not in self._infos:
            try:
                if self._v2:
                    record = store.source_record(self._row, key)
                    _require(record.checkpoint_run_version <= self._row.version)
                    entry = store.checkpoint(record)
                    self._bytes[key] = record.stored_bytes
                else:
                    entry = self._legacy_state[key]
                _require(type(entry) is dict and type(entry["metadata"]) is dict)
                count = entry["pageCount"]
                _require(type(count) is int and 1 <= count <= 2000)
                verifier = PageReconciler()
                _require(type(entry["verifier"]) is dict and set(entry["verifier"]) == set(verifier.__dict__))
                verifier.__dict__.update(deepcopy(entry["verifier"]))
                self._infos[key] = {"metadata": deepcopy(entry["metadata"]), "expected": verifier.result(), "pageCount": count}
                store.assert_current(self._row)
            except (AnalysisContractError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
                raise AiError("封存来源检查点未通过核验", "conflict", 409) from error
        return deepcopy(self._infos[key])

    def _identity(self, source, page, metadata, first):
        query, domain = source["query"], source["domain"]
        filters = page["filters"]
        periods = comparison_periods(query["startDate"], query["endDate"])
        window = query.get("window", "current")
        expected_filters = {k: v for k, v in query.items() if k not in {"startDate", "endDate", "window"}}
        expected_filters.update(window=window, periods=periods)
        if domain == "sales":
            expected_filters.update(startDate=query["startDate"], endDate=query["endDate"], limit=self._limit)
        elif domain == "market":
            expected_filters.update(shop="", limit=self._limit)
        # An old omitted window means current; no stored page is rewritten.
        _require(_same({"window": "current", **filters}, expected_filters))
        _require(page["monetaryUnit"] == "CNY_CENT" and page["sourceRevision"] == metadata["sourceRevision"])
        _require(type(page["sourceRevision"]) is str and 1 <= len(page["sourceRevision"]) <= 128)
        pagination = page["pagination"]
        _require(type(pagination["limit"]) is int and pagination["limit"] == self._limit)
        _require(type(pagination["hasMore"]) is bool)
        _require(pagination["nextCursor"] is None or type(pagination["nextCursor"]) is str)
        _require(type(page["items"]) is list and len(page["items"]) <= self._limit)
        _require(type(page["pageEvidence"]["rowCount"]) is int)
        if first:
            _require(all(_same(page.get(k), metadata.get(k)) for k in METADATA_FIELDS))
        else:
            _require(_same(page.get("metricSemantics"), metadata.get("metricSemantics")))
            _require(all(page.get(k) is None for k in METADATA_FIELDS if k != "metricSemantics"))
        master = domain == "netshop" and query.get("dataset") == "master"
        if domain == "netshop":
            from netshop.analysis import SOURCES
            _require((page["source"], page["sourceDataset"]) == SOURCES[query["dataset"]][query["platform"]])
            if master:
                coverage = metadata["coverage"]
                _require(coverage["historicalMapping"] is False and coverage["status"] in {"current_master", "no_records"})
        elif domain == "sales":
            _require(page["source"] == "erp_sales" and page.get("sourceDataset") is None)
            _require(all(filters.get(k) == query[k] for k in ("startDate", "endDate")))
        elif domain == "market":
            _require(page["source"] == page["sourceDataset"] == "market_daily_top" and filters.get("shop") == "")
        else:
            _require(False)
        if domain in {"sales", "market"}:
            _require(type(filters["limit"]) is int and filters["limit"] == self._limit)
        for item in page["items"]:
            _require(item["platform"] == query["platform"] and item["shopName"] == ("" if domain == "market" else query["shop"]))
            if domain == "sales":
                _require(item["channel"] == query["channel"])
            if domain == "market":
                _require(item["category"] == query["category"] and item["dimensions"]["marketScope"] == query["scope"])
                _require(item["spuId" if query["rankingDimension"] == "SKU" else "skuId"] is None)
            if master:
                _require(coverage["status"] == "current_master")
                _require(item["batchId"] == coverage["batchId"] and item["snapshotDate"] == coverage["snapshotDate"])
            else:
                value = item["date"]
                _require(type(value) is str and date.fromisoformat(value).isoformat() == value)
                _require(periods[window]["startDate"] <= value <= periods[window]["endDate"])

    def pages(self, key, checkpoint=None):
        info = self.info(key)
        source = self._by_key[key]
        verifier, count, stored_bytes = PageReconciler(), 0, 0
        try:
            for chunk in m.AiBusinessEvidenceChunk.objects.filter(run_id=self._row.id, source_key=key).order_by("sequence").iterator(chunk_size=10):
                count += 1
                _require(type(chunk.sequence) is int and chunk.sequence == count and count <= info["pageCount"])
                if checkpoint and count % 20 == 1:
                    checkpoint({"stage": "preparing", "sourceKey": key, "sourcePage": count})
                encoded = chunk.payload_json
                _require(type(encoded) is str and len(encoded) <= 131072)
                size = len(encoded.encode("utf-8"))
                _require(size <= 131072 and digest(encoded) == chunk.payload_digest)
                stored_bytes += size
                _require(stored_bytes <= 64*1024*1024)
                page = json.loads(encoded)
                self._identity(source, page, info["metadata"], count == 1)
                verifier.consume(page, request_cursor=verifier.expected_cursor)
                yield page
            _require(count == info["pageCount"] and _same(verifier.result(), info["expected"]))
            if self._v2:
                _require(stored_bytes == self._bytes[key])
            store.assert_current(self._row)
        except (AnalysisContractError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
            raise AiError("封存明细未通过完整链核验", "conflict", 409) from error

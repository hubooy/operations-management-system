"""Internal, read-only ERP SKU/SPU analysis over explicit sealed source pairs.

No route, report profile, or cache is registered here. Consume a table within
analyzed(), and publish only after normal context exit performs the final owner
and sealed-version check. Describing a source never certifies unread facts.
"""
from contextlib import contextmanager
import re

from business_analysis import mapped_results, mapping_plan
from business_analysis.contracts import AnalysisContractError
from business_analysis.partitioned import Checkpoint
from . import business_evidence, business_evidence_store as store, business_identity
from .business_sealed import Reader
from .policy import AiError, canonical, digest, fields, identifier

MAX_RESPONSE_BYTES = 65536


@contextmanager
def table(run_id, plan, pair_key, dimension, principal, *, baseline_pair_key=None, checkpoint=None):
    """Fixed-plan entry for future report consumers, never an authority bypass.

    The caller must obtain plan from its immutable report snapshot. This checks
    its complete structure and source compatibility again; source key hashes
    alone do not bind evidence contents, which the live table also verifies.
    """
    checkpoint = Checkpoint.wrap(checkpoint)
    options = {"checkpoint":checkpoint} if checkpoint is not None else {}
    row = business_evidence.get_run(run_id, principal)
    reader = Reader(row, principal)
    try:
        checked = mapping_plan._checked_plan(plan, reader.sources)
        pairs = {pair["pairKey"]: pair for pair in checked["pairs"]}
        pair = pairs.get(identifier(pair_key))
        baseline = pairs.get(identifier(baseline_pair_key)) if baseline_pair_key is not None else None
        if pair is None or baseline_pair_key is not None and baseline is None:
            raise AiError("映射引用不在固定关联计划中", "conflict", 409)
        if baseline is not None:
            mapping_plan.validate_baseline_pair(reader.sources, checked, pair_key, baseline_pair_key)
        with analyzed(run_id, pair["salesKey"], pair["masterKey"], dimension, principal,
                baseline={key: baseline[key] for key in ("salesKey", "masterKey")} if baseline else None,**options) as (value, _):
            business_identity._current(row, principal)
            yield value
            business_identity._current(row, principal)
    except BaseException as error:
        if checkpoint is not None: checkpoint.raise_if_failed()
        if isinstance(error,(AnalysisContractError, KeyError, TypeError, ValueError)):
            raise AiError("固定映射计划未通过完整核验", "conflict", 409) from error
        raise


def _source(run_id, pair, principal, *, checkpoint=None):
    _, reader, sales, master, binding = business_identity.describe(
        run_id, pair["salesKey"], pair["masterKey"], principal)

    def open_mapping(*, max_scratch_bytes):
        return business_identity.reconciled(run_id, sales["key"], master["key"], principal,
            max_scratch_bytes=max_scratch_bytes,**({"checkpoint":checkpoint} if checkpoint is not None else {}))

    return mapped_results.MappingSource(binding, sales, master, reader.info(sales["key"]),
        reader.info(master["key"]), open_mapping)


@contextmanager
def analyzed(run_id, sales_key, master_key, dimension, principal, *, baseline=None, checkpoint=None):
    """Yield (live_table, fixed_binding) after both windows are fully verified.

    baseline is an explicit {salesKey,masterKey}, never an inferred source. The
    pure mapped_table serially opens each pair with its combined scratch limit;
    Reader.pages is traversed once per sales/master pair, not during describe.
    """
    checkpoint = Checkpoint.wrap(checkpoint)
    options = {"checkpoint":checkpoint} if checkpoint is not None else {}
    if type(dimension) is not str or dimension not in {"sku", "spu"}:
        raise AiError("ERP商品映射分析仅支持SKU或SPU")
    row = business_evidence.get_run(run_id, principal)
    if row.status != "sealed" or not store.is_v2(row):
        raise AiError("ERP商品映射分析需要已封存v2证据", "conflict", 409)
    reader = Reader(row, principal)
    pair = {"salesKey": identifier(sales_key), "masterKey": identifier(master_key)}
    pairs = [pair]
    if baseline is not None:
        fields(baseline, {"salesKey", "masterKey"}, {"salesKey", "masterKey"})
        baseline = {key: identifier(baseline[key]) for key in ("salesKey", "masterKey")}
        pairs.append(baseline)
    try:
        built = mapping_plan.build(reader.sources, pairs)
        by_sales = {item["salesKey"]: item for item in built["plan"]["pairs"]}
        current_key = by_sales[sales_key]["pairKey"]
        comparison = (mapping_plan.validate_baseline_pair(reader.sources, built["plan"], current_key,
            by_sales[baseline["salesKey"]]["pairKey"]) if baseline is not None else None)
        current = _source(run_id, pair, principal,**options)
        previous = _source(run_id, baseline, principal,**options) if baseline is not None else None
        with mapped_results.mapped_table(current, dimension, baseline=previous,**options) as table:
            business_identity._current(row, principal)
            header = table.header()
            binding = {"schemaVersion": "business-mapped-analysis-binding-v1", **built,
                "evidenceRunId": row.id, "evidenceVersion": row.version,
                "evidencePlanDigest": digest(row.plan_json),
                "catalogDigest": current.descriptor["binding"]["catalogDigest"],
                "sealedDigest": current.descriptor["binding"]["sealedDigest"],
                "tableAlgorithmVersion": mapped_results.ALGORITHM_VERSION,
                "dimension": dimension, "currentPairKey": current_key,
                "baselinePairKey": comparison["baselinePairKey"] if comparison else None,
                "tableBindingDigest": header["bindingDigest"]}
            yield table, binding
            business_identity._current(row, principal)
    except BaseException as error:
        if checkpoint is not None: checkpoint.raise_if_failed()
        if isinstance(error,(AnalysisContractError, KeyError, TypeError, ValueError)):
            raise AiError("ERP商品映射分析未通过完整范围或封存核验", "conflict", 409) from error
        raise


def page(run_id, params, principal):
    """Internal paging helper; deliberately not registered as an API route."""
    fields(params, {"salesKey", "masterKey", "dimension", "baselineSalesKey", "baselineMasterKey", "offset", "limit"},
        {"salesKey", "masterKey", "dimension"})
    offset = params.get("offset", "0")
    if (type(offset) is not str or len(offset) > 6 or re.fullmatch(r"0|[1-9][0-9]*", offset) is None
            or int(offset) > 250000 or params.get("limit", "20") != "20"):
        raise AiError("映射分析分页无效；页长固定为20")
    if ("baselineSalesKey" in params) != ("baselineMasterKey" in params):
        raise AiError("比较须显式指定销售与主数据两端来源")
    baseline = ({"salesKey": params["baselineSalesKey"], "masterKey": params["baselineMasterKey"]}
        if "baselineSalesKey" in params else None)
    with analyzed(run_id, params["salesKey"], params["masterKey"], params["dimension"], principal, baseline=baseline) as (table, binding):
        result = {"schemaVersion": "business-mapped-analysis-response-v1", "binding": binding,
            "bindingDigest": digest(binding), "table": table.page(offset=int(offset), limit=20)}
        if len(canonical(result).encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise AiError("完整ERP商品映射分析响应超过容量", "payload_too_large", 413)
    return result

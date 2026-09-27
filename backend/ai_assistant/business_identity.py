"""Explicit, read-only product pairs over fully reconciled sealed sources.

This adapter is intentionally separate from legacy /mapping and does not change
historical report snapshots or infer which master belongs to a sales source.
"""
import json
import re
from contextlib import contextmanager

from business_analysis.contracts import AnalysisContractError
from business_analysis import identity_partitioned
from business_analysis.partitioned import Checkpoint
from . import business_evidence, business_evidence_store as store
from .business_sealed import Reader
from .policy import AiError, canonical, current_principal, digest, fields, identifier

MAX_RESPONSE_BYTES = 65536


def _current(row, principal):
    current_principal(principal, admin=True)
    actual = business_evidence.get_run(row.id, principal)
    if (actual.version, actual.plan_json, actual.state_json, actual.status) != (
            row.version, row.plan_json, row.state_json, "sealed"):
        raise AiError("商品关联期间证据绑定变化", "version_conflict", 409)
    store.assert_current(row)


def describe(run_id, sales_key, master_key, principal):
    """Internal trusted descriptors only; does not certify any fact traversal.

    The actual opener must still run reconciled() and match its final binding.
    Kept here so derived readers never duplicate or trust client-supplied source
    binding construction. No source pages, mapping result, or model are read.
    """
    sales_key, master_key = identifier(sales_key), identifier(master_key)
    row = business_evidence.get_run(run_id, principal)
    if row.status != "sealed" or not store.is_v2(row):
        raise AiError("分区商品关联需要已封存的 v2 证据", "conflict", 409)
    reader = Reader(row, principal)
    sources = {source["key"]: source for source in reader.sources}
    sales, master = sources.get(sales_key), sources.get(master_key)
    if (sales is None or master is None or sales_key == master_key or sales["domain"] != "sales"
            or master["domain"] != "netshop" or master["query"].get("dataset") != "master"
            or master["query"].get("window", "current") != "current"):
        raise AiError("须显式选择 ERP 销售及本期商品主数据来源")
    if any(sales["query"].get(key) != master["query"].get(key) for key in ("platform", "shop")):
        raise AiError("销售和主数据必须属于同一精确平台、店铺")
    expected = {"sales": reader.info(sales_key)["expected"], "master": reader.info(master_key)["expected"]}
    header, state = json.loads(row.plan_json), json.loads(row.state_json)
    binding = {"schemaVersion": "business-product-mapping-binding-v1", "evidenceRunId": row.id,
        "evidenceVersion": row.version, "evidencePlanDigest": digest(row.plan_json),
        "catalogDigest": header["catalogDigest"], "sealedDigest": state["sealedDigest"],
        "algorithmVersion": identity_partitioned.ALGORITHM_VERSION,
        "sales": {"sourceKey": sales_key, "queryDigest": digest(sales["query"]), "sourceRef": expected["sales"]["sourceRef"]},
        "master": {"sourceKey": master_key, "queryDigest": digest(master["query"]), "sourceRef": expected["master"]["sourceRef"]}}
    _current(row, principal)
    return row, reader, sales, master, binding


@contextmanager
def reconciled(run_id, sales_key, master_key, principal, *, max_scratch_bytes=None, checkpoint=None):
    """Yield only after both exact sources have been consumed in full."""
    checkpoint = Checkpoint.wrap(checkpoint)
    options = {"checkpoint":checkpoint} if checkpoint is not None else {}
    if checkpoint is not None: checkpoint({"stage":"identity_descriptor","phase":"before"})
    row, reader, sales, master, binding = describe(run_id, sales_key, master_key, principal)
    if checkpoint is not None: checkpoint({"stage":"identity_descriptor","phase":"after"})
    sales_key, master_key = sales["key"], master["key"]
    expected = {"sales": reader.info(sales_key)["expected"], "master": reader.info(master_key)["expected"]}
    try:
        with identity_partitioned.reconcile_products(reader.pages(sales_key,**options), reader.pages(master_key,**options),
                sales_expected=expected["sales"], master_expected=expected["master"],
                **options,
                **({"max_scratch_bytes": max_scratch_bytes} if max_scratch_bytes is not None else {})) as result:
            _current(row, principal)
            yield result, binding
            _current(row, principal)
    except BaseException as error:
        if checkpoint is not None: checkpoint.raise_if_failed()
        if isinstance(error,(AnalysisContractError, ValueError, TypeError, KeyError)):
            raise AiError("商品关联未通过完整封存核验", "conflict", 409) from error
        raise


def page(run_id, params, principal):
    fields(params, {"salesKey", "masterKey", "offset", "limit"}, {"salesKey", "masterKey"})
    raw = params.get("offset", "0")
    if (type(raw) is not str or len(raw) > 6 or re.fullmatch(r"0|[1-9][0-9]*", raw) is None
            or int(raw) > 250000 or params.get("limit", "20") != "20"):
        raise AiError("商品关联分页无效；页长固定为20")
    with reconciled(run_id, params["salesKey"], params["masterKey"], principal) as (result, binding):
        payload = {"schemaVersion": "business-product-mapping-response-v2", "binding": binding,
            "bindingDigest": digest(binding), "mapping": result.page(offset=int(raw), limit=20)}
        if len(canonical(payload).encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise AiError("完整商品关联响应超过容量", "payload_too_large", 413)
    return payload

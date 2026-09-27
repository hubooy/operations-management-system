"""Append complete mapped tables to the existing bounded report spool."""
from business_analysis import mapping_plan
from . import business_identity, business_integrated, business_mapped_analysis
from .policy import canonical, digest


def _metadata(value):
    for key, item in value.items():
        text = canonical(item)
        for offset in range(0, max(1, len(text)), 7000):
            yield {"项目":key, "片段":offset//7000+1, "内容":text[offset:offset+7000]}


def append(spool, report, principal, *, checkpoint=None):
    actual, snapshot, _, evidence, sources = business_integrated.bound(report, principal)
    _append_bound(spool, snapshot, evidence, sources, principal, checkpoint=checkpoint)
    business_integrated.bound(actual, principal)


def _append_bound(spool, snapshot, evidence, sources, principal, *, checkpoint=None):
    """Internal only: owning caller must revalidate its fixed report before/after."""
    plan = snapshot["mappingPlan"]
    indexed = {s["key"]:s for s in sources}
    note = "固定关联选择；当前主数据回溯历史金额，不证明历史商品归属或广告归因。"
    spool.add("mapped-plan", "固定商品关联计划", note,
        ({"mappingPlanDigest":snapshot["mappingPlanDigest"], "algorithmVersion":plan["algorithmVersion"], **p} for p in plan["pairs"]), len(plan["pairs"]))
    for pair in plan["pairs"]:
        if checkpoint: checkpoint()
        key = pair["pairKey"]
        with business_identity.reconciled(evidence.id, pair["salesKey"], pair["masterKey"], principal) as (result, binding):
            summary = result.summary()
            spool.add("mapping-proof-"+key, "关联核对_"+pair["salesKey"], note,
                _metadata({"binding":binding, "summary":summary}))
            spool.add("mapping-groups-"+key, "完整关联_"+pair["salesKey"], note, result.scan(), summary["groupCount"])
        bases = [None]
        if indexed[pair["salesKey"]]["query"].get("window","current") == "current":
            for candidate in plan["pairs"]:
                source = indexed[candidate["salesKey"]]
                if (source["query"].get("window") in {"previous","yearAgo"}
                        and candidate["masterKey"] == pair["masterKey"]
                        and {k:v for k,v in source["query"].items() if k != "window"}
                            == {k:v for k,v in indexed[pair["salesKey"]]["query"].items() if k != "window"}):
                    mapping_plan.validate_baseline_pair(sources, plan, key, candidate["pairKey"])
                    bases.append(candidate["pairKey"])
        for dimension in ("sku","spu"):
            for base in bases:
                if checkpoint: checkpoint()
                with business_mapped_analysis.table(evidence.id, plan, key, dimension, principal, baseline_pair_key=base) as table:
                    header = table.header()
                    period = {"current":"本期","previous":"环比基期","yearAgo":"同比基期"}[header["sourceWindow"]]
                    if base: period = "环比" if header["comparisonWindow"] == "previous" else "同比"
                    identity = digest([key,dimension,base])
                    title = f'{pair["salesKey"]}_映射{dimension.upper()}_{period}'
                    spool.add("mapped-proof-"+identity, "核对_"+title, note, _metadata(header))
                    # A comparison with both missing sides occupies up to 154
                    # columns. Keep the two selectors here; proofs go in the
                    # separate table so the fixed 160-column cap remains real.
                    spool.add("mapped-analysis-"+identity, title, "；".join(header["limitations"]),
                        ({"pairKey":key, "baselinePairKey":base, **row} for row in table.scan()), header["total"])

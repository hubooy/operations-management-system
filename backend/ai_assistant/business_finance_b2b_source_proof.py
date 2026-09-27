"""Default-disabled owning adapter for two separate sealed-source proof rows.

This does not merge v3 finance and v2 B2B into one report or register a model
tool or renderer. Missing inputs remain not_supplied, never zero or absent in
the underlying business system.
"""
from __future__ import annotations

from business_analysis import finance_b2b_source_proof as projection
from business_analysis.contracts import AnalysisContractError

from . import business_b2b_report_material as b2b_owner
from . import business_erp_rollup_materials as erp_owner
from . import business_finance_v3_report_material as finance_owner
from .business_sealed import Reader
from .policy import AiError, current_principal, identifier


def prepare(principal, *, finance_intent_id=None, finance_source_key=None,
            b2b_report_id=None, shop=None, erp_pair_key=None,
            erp_sales_key=None, erp_master_key=None, enabled=False,
            checkpoint=None):
    """Read only selected sealed owning sources; no cross-report authority."""
    if enabled is not True:
        raise AiError("财报/B端来源证明表候选默认关闭", "conflict", 409)
    current_principal(principal, admin=True)
    if ((finance_intent_id is None) != (finance_source_key is None)
            or (b2b_report_id is None) != (shop is None)):
        raise AiError("财报或B端来源必须声明完整身份", "invalid_request", 400)
    erp_keys = (erp_pair_key, erp_sales_key, erp_master_key)
    if any(key is not None for key in erp_keys) and (not all(
            key is not None for key in erp_keys) or b2b_report_id is None):
        raise AiError("ERP销售必须指定同一B端报告及完整关联来源",
            "invalid_request", 400)
    finance = None
    b2b = None
    if finance_intent_id is not None:
        finance = finance_owner.prepare(identifier(finance_intent_id),
            identifier(finance_source_key), principal).value
    if b2b_report_id is not None:
        b2b = b2b_owner.prepare(identifier(b2b_report_id), shop, principal,
            checkpoint=checkpoint)
    erp, erp_source = None, None
    if erp_pair_key is not None:
        report_id = identifier(b2b_report_id)
        _, _, evidence, sources, fixed = erp_owner._bound(report_id, principal)
        sales_key = identifier(erp_sales_key)
        source = next((entry for entry in sources if entry["key"] == sales_key
            and entry["domain"] == "sales"), None)
        if source is None:
            raise AiError("ERP销售来源不属于同一报告", "conflict", 409)
        reader = Reader(evidence, principal)
        info = reader.info(sales_key)
        with erp_owner.prepare(report_id, identifier(erp_pair_key), sales_key,
                identifier(erp_master_key), principal,
                checkpoint=checkpoint) as prepared:
            erp = prepared.manifest
        if erp_owner._bound(report_id, principal)[4] != fixed:
            raise AiError("ERP和B端来源复核期间封存报告变化", "conflict", 409)
        erp_source = {"key": sales_key, "query": source["query"], "info": info}
    try:
        result = projection.build_candidate(finance=finance, b2b=b2b,
            erp=erp, erp_source=erp_source)
        table = projection.as_table(result)
    except (AnalysisContractError, KeyError, TypeError, ValueError,
            UnicodeError, RecursionError) as error:
        raise AiError("财报/B端完整来源材料不能形成不混算证明表",
            "conflict", 409) from error
    current_principal(principal, admin=True)
    return {"schemaVersion": "business-finance-b2b-owning-proof-candidate-v1",
        "financeIntentId": result["financeIntentId"],
        "b2bReportId": result["b2bReportId"],
        "erpReportId": result["erpReportId"],
        "erpB2bSameReportOwningVerified": erp is not None,
        "table": table, "candidate": result,
        "sameReportAuthorityVerified": False,
        "agentReadPersisted": False,
        "registeredRenderer": False}

"""Read-only, conservative audit of the protected AI logical restore contract.

This script never connects to PostgreSQL. A zero exit code only means that the
static blockers it knows about are absent; it never certifies a restore.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTECTED_ROLES = {
    "teruisi_ai_budget_v11_attestor",
    "teruisi_ai_budget_v11_key_owner",
    "teruisi_ai_budget_v11_publisher",
    "teruisi_ai_market_paid_adopter",
    "teruisi_ai_market_paid_reserver",
    "teruisi_ai_market_paid_starter",
    "teruisi_ai_budget_v11_attest_login",
    "teruisi_ai_budget_v11_sign_login",
    "teruisi_ai_budget_v11_publish_login",
    "teruisi_ai_market_rate_proposer",
    "teruisi_ai_market_cap_proposer",
    "teruisi_ai_market_proposal_revoker",
    "teruisi_ai_budget_v11_attestor_v2_login",
    "teruisi_ai_market_v6_topology_login",
    "teruisi_ai_budget_v11_download_v2_login",
    "teruisi_ai_market_v6_source_login",
}


def audit(root: Path = ROOT) -> dict[str, object]:
    helper = (root / "tools/postgres-consistent-backup.py").read_text(encoding="utf-8")
    operator = (root / "tools/django-postgres-maintenance.ps1").read_text(encoding="utf-8")
    migration = (root / "backend/ai_assistant/migrations/0068_business_promotion_budget_v11_verifier_receipt.py").read_text(encoding="utf-8")
    installer = (root / "tools/django-local-service.ps1").read_text(encoding="utf-8")
    issues: list[str] = []
    login_attestation_path = (root / "backend/ai_assistant/migrations/"
        "0073_business_promotion_budget_v11_login_attestation.py")
    if login_attestation_path.is_file():
        login_attestation = login_attestation_path.read_text(encoding="utf-8")
        login_sql_path = (root / "backend/ai_assistant/"
            "business_promotion_budget_v11_login_attestation_sql.py")
        login_sql = (login_sql_path.read_text(encoding="utf-8")
            if login_sql_path.is_file() else "")
        for source_name, source, marker in (
            ("formal PrepareApp", installer,
             "0073_business_promotion_budget_v11_login_attestation.py"),
            ("backup inventory", helper,
             "protected_business_budget_v11_login_attestations"),
            ("backup migration receipt", helper,
             "0073_business_promotion_budget_v11_login_attestation"),
            ("0073 catalog verifier", login_attestation,
             "def verify_catalog("),
            ("0073 protected table", login_attestation,
             "protected_business_budget_v11_login_attestations"),
            ("0073 protected role", login_attestation + login_sql,
             "teruisi_ai_budget_v11_attestor_v2_login"),
        ):
            if marker not in source:
                issues.append(source_name + " lacks exact 0073 protection")
        if '67|68|69|70|71|72|73|74' not in operator:
            issues.append("formal restore gate omits protected 0073 receipt")
    cap_approval_path = (root / "backend/ai_assistant/migrations/"
        "0074_business_market_v2_human_cap_approval.py")
    if cap_approval_path.is_file():
        cap_source = cap_approval_path.read_text(encoding="utf-8")
        for source_name, source, marker in (
            ("formal PrepareApp", installer,
             "0074_business_market_v2_human_cap_approval.py"),
            ("backup inventory", helper,
             "protected_business_market_v2_human_cap_approvals"),
            ("backup migration receipt", helper,
             "0074_business_market_v2_human_cap_approval"),
            ("0074 catalog verifier", cap_source, "def verify_catalog("),
        ):
            if marker not in source:
                issues.append(source_name + " lacks exact 0074 protection")
        if '67|68|69|70|71|72|73|74' not in operator:
            issues.append("formal restore gate omits protected 0074 receipt")
    report_page_path = (root / "backend/ai_assistant/migrations/"
        "0075_business_v4_report_restricted_page.py")
    if report_page_path.is_file():
        page_source = report_page_path.read_text(encoding="utf-8")
        for source_name, source, marker in (
            ("formal PrepareApp", installer,
             "0075_business_v4_report_restricted_page.py"),
            ("backup migration receipt", helper,
             "0075_business_v4_report_restricted_page"),
            ("0075 catalog verifier", page_source, "def verify_catalog("),
            ("0075 restricted function", page_source,
             "business_v4_report_restricted_page_sql"),
        ):
            if marker not in source:
                issues.append(source_name + " lacks exact 0075 protection")
        if '67|68|69|70|71|72|73|74|75' not in operator:
            issues.append("formal restore gate omits protected 0075 receipt")
    ticket_sign_path = (root / "backend/ai_assistant/migrations/"
        "0076_business_promotion_budget_v11_ticket_bound_signer.py")
    if ticket_sign_path.is_file():
        ticket_source = ticket_sign_path.read_text(encoding="utf-8")
        for source_name, source, marker in (
            ("formal PrepareApp", installer,
             "0076_business_promotion_budget_v11_ticket_bound_signer.py"),
            ("backup inventory", helper,
             "protected_business_budget_v11_signed_receipts_v3"),
            ("backup migration receipt", helper,
             "0076_business_promotion_budget_v11_ticket_bound_signer"),
            ("0076 catalog verifier", ticket_source, "def verify_catalog("),
        ):
            if marker not in source:
                issues.append(source_name + " lacks exact 0076 protection")
        if '67|68|69|70|71|72|73|74|75|76' not in operator:
            issues.append("formal restore gate omits protected 0076 receipt")
    topology_path = (root / "backend/ai_assistant/migrations/"
        "0077_business_market_v6_paused_topology.py")
    if topology_path.is_file():
        topology_source = topology_path.read_text(encoding="utf-8")
        for source_name, source, marker in (
            ("formal PrepareApp", installer,
             "0077_business_market_v6_paused_topology.py"),
            ("backup inventory", helper,
             "protected_business_market_v6_topology_cancellations"),
            ("backup migration receipt", helper,
             "0077_business_market_v6_paused_topology"),
            ("0077 catalog verifier", topology_source, "def verify_catalog("),
        ):
            if marker not in source:
                issues.append(source_name + " lacks exact 0077 protection")
        if '67|68|69|70|71|72|73|74|75|76|77' not in operator:
            issues.append("formal restore gate omits protected 0077 receipt")
    publication_path=(root / "backend/ai_assistant/migrations/"
        "0078_business_promotion_budget_v11_signed_publication.py")
    if publication_path.is_file():
        publication_source=publication_path.read_text(encoding="utf-8")
        for source_name, source, marker in (
            ("formal PrepareApp",installer,
             "0078_business_promotion_budget_v11_signed_publication.py"),
            ("backup inventory",helper,
             "protected_business_budget_v11_publications_v2"),
            ("backup migration receipt",helper,
             "0078_business_promotion_budget_v11_signed_publication"),
            ("0078 catalog verifier",publication_source,"def verify_catalog("),
        ):
            if marker not in source:
                issues.append(source_name+" lacks exact 0078 protection")
        if '67|68|69|70|71|72|73|74|75|76|77|78' not in operator:
            issues.append("formal restore gate omits protected 0078 receipt")
    source_ticket_path = (root / "backend/ai_assistant/migrations/"
        "0079_business_market_v6_source_ticket.py")
    if source_ticket_path.is_file():
        source_ticket = source_ticket_path.read_text(encoding="utf-8")
        for source_name, text, marker in (
            ("formal PrepareApp", installer,
             "0079_business_market_v6_source_ticket.py"),
            ("backup inventory", helper,
             "protected_business_market_v6_source_tickets"),
            ("backup migration receipt", helper,
             "0079_business_market_v6_source_ticket"),
            ("0079 catalog verifier", source_ticket, "def verify_catalog("),
        ):
            if marker not in text:
                issues.append(source_name + " lacks exact 0079 protection")
        if '67|68|69|70|71|72|73|74|75|76|77|78|79' not in operator:
            issues.append("formal restore gate omits protected 0079 receipt")

    roles_block = re.search(r"\$MaintenanceRehearsalRoles\s*=\s*@\((.*?)\)", operator, re.S)
    actual_roles = set(re.findall(r'"(teruisi_[a-z0-9_]+)"', roles_block.group(1))) if roles_block else set()
    missing = sorted(PROTECTED_ROLES - actual_roles)
    if missing:
        issues.append("isolated restore lacks protected NOLOGIN role preflight: " + ",".join(missing))
    if re.search(r'\^teruisi_\[a-z_\]', operator) and any(
            not re.fullmatch(r"teruisi_[a-z_]{1,64}", role)
            for role in PROTECTED_ROLES):
        issues.append("restore role-name validator rejects versioned protected roles")

    backup = helper.partition("def run_backup(")[2].partition("def run_probe(")[0]
    restore = helper.partition("def run_restore(")[2].partition("def build_parser(")[0]
    dump_flags = re.search(r"FORMAL_DUMP_FLAGS\s*=\s*\((.*?)\)", helper, re.S)
    restore_flags = re.search(r"FORMAL_RESTORE_FLAGS\s*=\s*\((.*?)\)", helper, re.S)
    if ('"--no-privileges"' in backup or
            (dump_flags and '"--no-privileges"' in dump_flags.group(1)
             and "*FORMAL_DUMP_FLAGS" in backup)):
        issues.append("custom backup suppresses ACL entries")
    if ('"--no-owner"' in restore or '"--no-privileges"' in restore or
            (restore_flags and "*FORMAL_RESTORE_FLAGS" in restore and
             ('"--no-owner"' in restore_flags.group(1) or
              '"--no-privileges"' in restore_flags.group(1)))):
        issues.append("restore suppresses recorded object owners or ACLs")
    if ("PGUSER = \"teruisi_sales_owner\"" in operator
            and "ALTER TABLE " + '" + KEY_TABLE + " OWNER TO "' in migration
            and "GRANT SELECT ON " + '" + KEY_TABLE' not in migration):
        issues.append("backup owner has no proven SELECT on isolated verifier key table")
    if ("NOCREATEROLE" in installer and "CREATE ROLE " in migration
            and "GRANT " + '" + KEY_OWNER + " TO "' in migration):
        issues.append("ordinary migration owner cannot create or temporarily grant protected role")
    if "REVOKE " + '" + KEY_OWNER + " FROM "' in migration and "SELECT count(*) FROM " in migration:
        issues.append("0068 installer reads private key table after revoking its temporary membership")

    return {
        "scope": "static source only; no database connection or restore",
        "status": "blocked" if issues else "requires_isolated_cross_cluster_rehearsal",
        "issues": issues,
    }


if __name__ == "__main__":
    result = audit()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    raise SystemExit(2 if result["issues"] else 0)

"""Fixed, test-only protected cross-cluster generations.

The default 0072 contract is frozen for the existing rehearsal. 0073 and
0074 must be requested explicitly; no generation is a formal backup contract.
"""
from __future__ import annotations

from dataclasses import dataclass


BASE_ROLES = frozenset({
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
})
BASE_MIGRATIONS = (
    "0068_business_promotion_budget_v11_verifier_receipt",
    "0069_business_market_v2_paid_round_rehearsal",
    "0070_business_promotion_budget_v11_limited_identity",
    "0071_business_v4_report_source_link",
    "0072_business_market_v2_authority_proposals",
)
LOGIN_ROLE = "teruisi_ai_budget_v11_attestor_v2_login"
LOGIN_TABLE = "protected_business_budget_v11_login_attestations"
LOGIN_MIGRATION = "0073_business_promotion_budget_v11_login_attestation"
CAP_APPROVAL = "protected_business_market_v2_human_cap_approvals"
CAP_REVOCATION = "protected_business_market_v2_human_cap_revocations"
CAP_MIGRATION = "0074_business_market_v2_human_cap_approval"


@dataclass(frozen=True)
class Generation:
    name: str
    seed_file: str
    upgrade: str
    roles: frozenset[str]
    migrations: tuple[str, ...]
    table_count: int


def contract(name: str = "0072") -> Generation:
    if name == "0072":
        return Generation(name, "business-market-v2-authority-upgrade-evidence.json",
            "0071->0072", BASE_ROLES, BASE_MIGRATIONS, 8)
    if name == "0073":
        return Generation(name, "business-v11-login-attestation-upgrade-evidence.json",
            "0072->0073", BASE_ROLES | {LOGIN_ROLE},
            (*BASE_MIGRATIONS, LOGIN_MIGRATION), 9)
    if name == "0074":
        return Generation(name, "business-market-v2-human-cap-upgrade-evidence.json",
            "0073->0074", BASE_ROLES | {LOGIN_ROLE},
            (*BASE_MIGRATIONS, LOGIN_MIGRATION, CAP_MIGRATION), 11)
    raise ValueError("unknown protected cross-cluster generation")


def seed_matches(seed: object, generation: Generation) -> bool:
    if not isinstance(seed, dict):
        return False
    if (seed.get("upgrade") != generation.upgrade
            or seed.get("afterBackupRestored") is not True
            or seed.get("emptyReverseAndReapply") is not True):
        return False
    if generation.name == "0073":
        return (seed.get("beforeBackupRestored") is True
            and seed.get("defaultRoleNoLoginAndNoPassword") is True
            and seed.get("newAttestationRows") == 0
            and seed.get("productionWrites") is False)
    if generation.name == "0074":
        return (seed.get("beforeBackupRestored") is True
            and seed.get("oldFunctionOidBodyAclOwnerPreserved") is True
            and seed.get("newApprovals") == 0
            and seed.get("productionWrites") is False)
    return True

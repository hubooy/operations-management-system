"""Launch an isolated PostgreSQL cluster; production ports/data are never used."""

from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--all-backend-tests", action="store_true")
parser.add_argument("--tests-only", action="store_true", help="Run AI tests in an isolated cluster without historical migration rehearsal")
parser.add_argument("--preprovision-ai-runtime-roles", action="store_true",
                    help="Isolated tests only: create reader/writer roles before migrations")
parser.add_argument("--business-v4-report-restricted-reader-login",
    action="store_true", help="Test only: give the isolated ai_reader a synthetic login password and read-only default")
parser.add_argument("--test-label", action="append", default=[], help="Explicit Django test labels; only with --tests-only, without upgrade flags")
parser.add_argument("--test-timeout-seconds", type=int, default=300,
                    help="Bounded Django test duration for large isolated suites (60-1800)")
parser.add_argument("--test-verbosity", type=int, choices=(1, 2), default=1)
parser.add_argument("--generation-upgrade", action="store_true", help="Rehearse 0008 to 0009 in the fresh isolated database before testing")
parser.add_argument("--prompt-settings-upgrade", action="store_true", help="Rehearse 0010 to 0011, roles and backup restoration in the fresh isolated database")
parser.add_argument("--report-library-upgrade", action="store_true")
parser.add_argument("--business-evidence-upgrade", action="store_true")
parser.add_argument("--market-options-upgrade", action="store_true")
parser.add_argument("--sales-options-upgrade", action="store_true")
parser.add_argument("--business-file-opc-upgrade", action="store_true")
parser.add_argument("--business-promotion-profile-upgrade", action="store_true")
parser.add_argument("--business-promotion-file-guard-upgrade", action="store_true")
parser.add_argument("--business-finance-v3-upgrade", action="store_true")
parser.add_argument("--business-promotion-file-ready-upgrade", action="store_true")
parser.add_argument("--business-finance-v3-pages-upgrade", action="store_true")
parser.add_argument("--business-v3-daily-pages-upgrade", action="store_true")
parser.add_argument("--business-v3-tool-receipts-upgrade", action="store_true")
parser.add_argument("--business-v3-parent-seal-upgrade", action="store_true")
parser.add_argument("--business-v3-report-intent-upgrade", action="store_true")
parser.add_argument("--business-v4-ledger-upgrade", action="store_true")
parser.add_argument("--business-v4-validation-upgrade", action="store_true")
parser.add_argument("--business-v4-seal-admission-upgrade", action="store_true")
parser.add_argument("--business-v4-seal-writer-upgrade", action="store_true")
parser.add_argument("--business-v4-sealer-ledger-read-upgrade", action="store_true")
parser.add_argument("--business-v4-sealer-narrow-stream-upgrade", action="store_true")
parser.add_argument("--business-v4-seal-ticket-upgrade", action="store_true")
parser.add_argument("--business-v4-claimed-read-upgrade", action="store_true")
parser.add_argument("--business-v4-seal-consumption-upgrade", action="store_true")
parser.add_argument("--business-market-v2-parked-upgrade", action="store_true")
parser.add_argument("--business-market-v2-material-upgrade", action="store_true")
parser.add_argument("--business-promotion-trial-file-upgrade", action="store_true")
parser.add_argument("--business-v4-replay-progress-upgrade", action="store_true")
parser.add_argument("--business-v4-finance-replay-progress-upgrade", action="store_true")
parser.add_argument("--business-v4-sealer-source-bridge-upgrade", action="store_true")
parser.add_argument("--business-v4-replay-read-cast-upgrade", action="store_true")
parser.add_argument("--business-v4-prior-claim-qualification-upgrade", action="store_true")
parser.add_argument("--business-v4-commit-consumption-upgrade", action="store_true")
parser.add_argument("--business-market-v2-admitted-paused-upgrade", action="store_true")
parser.add_argument("--business-promotion-budget-v10-stage-upgrade", action="store_true")
parser.add_argument("--business-v4-period-plan-upgrade", action="store_true")
parser.add_argument("--business-market-v2-role-bridge-upgrade", action="store_true")
parser.add_argument("--business-promotion-budget-v10-attestation-upgrade", action="store_true")
parser.add_argument("--business-promotion-budget-v10-publish-upgrade", action="store_true")
parser.add_argument("--business-promotion-budget-v10-reader-fence-upgrade", action="store_true")
parser.add_argument("--business-market-v2-execution-snapshot-upgrade", action="store_true")
parser.add_argument("--business-market-v2-context-proof-upgrade", action="store_true")
parser.add_argument("--business-market-v2-read-receipt-upgrade", action="store_true")
parser.add_argument("--business-market-v2-execution-plan-upgrade", action="store_true")
parser.add_argument("--business-market-v2-synthetic-upgrade", action="store_true")
parser.add_argument("--business-market-v2-cost-upgrade", action="store_true")
parser.add_argument("--business-promotion-budget-v11-stage-upgrade", action="store_true")
parser.add_argument("--business-promotion-budget-v11-attestation-upgrade", action="store_true")
parser.add_argument("--business-promotion-budget-v11-verifier-upgrade", action="store_true")
parser.add_argument("--business-market-v2-paid-round-upgrade", action="store_true")
parser.add_argument("--business-promotion-budget-v11-identity-upgrade", action="store_true")
parser.add_argument("--business-v4-report-link-upgrade", action="store_true")
parser.add_argument("--business-market-v2-authority-upgrade", action="store_true")
parser.add_argument("--business-v11-login-attestation-upgrade", action="store_true",
                    help="Test only: 0072->0073 isolated LOGIN sidecar upgrade and rollback")
parser.add_argument("--business-market-human-cap-upgrade", action="store_true",
                    help="Test only: 0073->0074 explicit CNY cap upgrade and rollback")
parser.add_argument("--business-v4-report-restricted-page-upgrade",
    action="store_true", help="Test only: 0074->0075 restricted read upgrade and dual restore")
parser.add_argument("--business-v4-report-restricted-page-focused-upgrade",
    action="store_true", help="Test only: current finance.0005 0075 empty reverse and restored upgrade")
parser.add_argument("--business-v11-ticket-sign-focused-upgrade",
    action="store_true", help="Test only: current finance.0005 0075->0076 empty reverse and dual restore")
parser.add_argument("--business-market-v6-topology-focused-upgrade",
    action="store_true", help="Test only: 0076->0077 paused five-job topology empty reverse and dual restore")
parser.add_argument("--business-v11-publication-focused-upgrade",
    action="store_true", help="Test only: 0077->0078 signed sidecar empty reverse and dual restore")
parser.add_argument("--business-market-v6-source-ticket-focused-upgrade",
    action="store_true", help="Test only: 0078->0079 source ticket empty reverse and dual restore")
parser.add_argument("--finance-raw-workbook-upgrade", action="store_true",
    help="Test only: finance.0005->0006 empty upgrade and dual restore")
parser.add_argument("--business-protected-cross-cluster-restore-0074", action="store_true",
                    help="Test only: restore 0074 owner/ACL into a second fresh encrypted cluster")
parser.add_argument("--business-protected-cross-cluster-restore", action="store_true",
                    help="Test only: follow 0072 with a second fresh cluster and preserve protected owners/ACL")
parser.add_argument("--business-protected-cross-cluster-restore-0073", action="store_true",
                    help="Test only: follow 0073 with a second fresh encrypted cluster; old 0072 path unchanged")
parser.add_argument("--protected-archive-layout",
                    choices=("whole-v2", "stream-v1"), default="whole-v2",
                    help="Test only: explicit 0073 cross-cluster archive layout")
parser.add_argument("--business-protected-shadow-snapshot-0073", action="store_true",
                    help="Test only: explicit frozen 0073 exported-snapshot encrypted restore")
parser.add_argument("--shadow-target-port", type=int,
                    help="Test only: distinct loopback port for the frozen 0073 shadow target")
parser.add_argument("--business-protected-migration-role-rehearsal", action="store_true",
                    help="Test only: probe 0067-0072 with a real synthetic non-superuser migration login")
parser.add_argument("--business-protected-installer-focus", action="store_true",
                    help="Test only: forward 0066 and current finance 0005, then isolated seven-step installer")
parser.add_argument("--source-revision-guards-upgrade", action="store_true")
parser.add_argument("--upgrade-only", action="store_true", help="Run the full selected upgrade/restore rehearsal; run tests separately with --tests-only")
parser.add_argument("--port", type=int, default=55443, help="Independent rehearsal port (55440-55999)")
arguments = parser.parse_args()
if arguments.business_protected_shadow_snapshot_0073:
    # Reject unrelated modes before any run root, credential, or cluster exists.
    allowed = {"business_protected_shadow_snapshot_0073", "upgrade_only"}
    if (not arguments.upgrade_only
            or any(value is True and name not in allowed
                for name, value in vars(arguments).items())
            or arguments.test_label
            or arguments.shadow_target_port is None
            or not 55440 <= arguments.shadow_target_port <= 55999
            or arguments.shadow_target_port == arguments.port):
        parser.error("Frozen 0073 shadow requires only --upgrade-only and a distinct isolated target port")
    migrations = ROOT / "backend/ai_assistant/migrations"
    if (not (migrations /
            "0073_business_promotion_budget_v11_login_attestation.py").is_file()
            or any(path.name[:4].isdigit() and int(path.name[:4]) > 73
                for path in migrations.glob("*.py"))):
        parser.error("Frozen 0073 shadow rejects later AI migration source")
    if shutil.disk_usage(ROOT).free < 12 * 1024**3:
        parser.error("Frozen 0073 shadow requires 12 GiB free before initdb")
    with socket.socket() as target_probe:
        try:
            target_probe.bind(("127.0.0.1", arguments.shadow_target_port))
        except OSError:
            parser.error("Frozen 0073 shadow target port is occupied")
    arguments.business_v11_login_attestation_upgrade = True
elif arguments.shadow_target_port is not None:
    parser.error("--shadow-target-port requires the explicit 0073 shadow flag")
if arguments.business_market_v6_source_ticket_focused_upgrade:
    if (arguments.tests_only or arguments.upgrade_only or arguments.test_label
            or arguments.all_backend_tests
            or any(getattr(arguments, name) for name in vars(arguments)
                if name.endswith("_upgrade") and name !=
                "business_market_v6_source_ticket_focused_upgrade")
            or any(getattr(arguments, name) for name in (
                "business_protected_cross_cluster_restore",
                "business_protected_cross_cluster_restore_0073",
                "business_protected_cross_cluster_restore_0074",
                "business_protected_migration_role_rehearsal",
                "business_protected_installer_focus"))):
        parser.error("0079 focused upgrade is one isolated standalone run")
    arguments.preprovision_ai_runtime_roles = True
if arguments.business_v11_publication_focused_upgrade:
    if (arguments.tests_only or arguments.upgrade_only or arguments.test_label
            or arguments.all_backend_tests
            or any(getattr(arguments, name) for name in vars(arguments)
                if name.endswith("_upgrade") and name !=
                "business_v11_publication_focused_upgrade")
            or any(getattr(arguments, name) for name in (
                "business_protected_cross_cluster_restore",
                "business_protected_cross_cluster_restore_0073",
                "business_protected_cross_cluster_restore_0074",
                "business_protected_migration_role_rehearsal",
                "business_protected_installer_focus"))):
        parser.error("0078 focused upgrade is one isolated standalone run")
    arguments.preprovision_ai_runtime_roles = True
if arguments.business_market_v6_topology_focused_upgrade:
    if (arguments.tests_only or arguments.upgrade_only or arguments.test_label
            or arguments.all_backend_tests
            or any(getattr(arguments, name) for name in vars(arguments)
                if name.endswith("_upgrade") and name !=
                "business_market_v6_topology_focused_upgrade")
            or any(getattr(arguments, name) for name in (
                "business_protected_cross_cluster_restore",
                "business_protected_cross_cluster_restore_0073",
                "business_protected_cross_cluster_restore_0074",
                "business_protected_migration_role_rehearsal",
                "business_protected_installer_focus"))):
        parser.error("0077 focused upgrade is one isolated standalone run")
    arguments.preprovision_ai_runtime_roles = True
if arguments.business_v11_ticket_sign_focused_upgrade:
    if (arguments.tests_only or arguments.upgrade_only or arguments.test_label
            or arguments.all_backend_tests
            or any(getattr(arguments, name) for name in vars(arguments)
                if name.endswith("_upgrade") and name !=
                "business_v11_ticket_sign_focused_upgrade")
            or any(getattr(arguments, name) for name in (
                "business_protected_cross_cluster_restore",
                "business_protected_cross_cluster_restore_0073",
                "business_protected_cross_cluster_restore_0074",
                "business_protected_migration_role_rehearsal",
                "business_protected_installer_focus"))):
        parser.error("0076 focused upgrade is one isolated standalone run")
    arguments.preprovision_ai_runtime_roles = True
if arguments.finance_raw_workbook_upgrade:
    if (arguments.tests_only or arguments.upgrade_only or arguments.test_label
            or arguments.all_backend_tests
            or any(getattr(arguments, name) for name in vars(arguments)
                if name.endswith("_upgrade") and name !=
                "finance_raw_workbook_upgrade")
            or any(getattr(arguments, name) for name in (
                "business_protected_cross_cluster_restore",
                "business_protected_cross_cluster_restore_0073",
                "business_protected_cross_cluster_restore_0074",
                "business_protected_migration_role_rehearsal",
                "business_protected_installer_focus"))):
        parser.error("finance.0006 upgrade is one isolated standalone run")
if arguments.business_v4_report_restricted_page_focused_upgrade:
    if (arguments.tests_only or arguments.upgrade_only or arguments.test_label
            or arguments.all_backend_tests
            or any(getattr(arguments, name) for name in vars(arguments)
                if name.endswith("_upgrade") and name !=
                "business_v4_report_restricted_page_focused_upgrade")
            or any(getattr(arguments, name) for name in (
                "business_protected_cross_cluster_restore",
                "business_protected_cross_cluster_restore_0073",
                "business_protected_cross_cluster_restore_0074",
                "business_protected_migration_role_rehearsal",
                "business_protected_installer_focus"))):
        parser.error("0075 focused upgrade is one isolated standalone run")
    arguments.preprovision_ai_runtime_roles = True
if arguments.business_protected_installer_focus and (
        arguments.tests_only or arguments.test_label or arguments.upgrade_only
        or arguments.business_protected_migration_role_rehearsal
        or arguments.business_protected_cross_cluster_restore
        or arguments.business_protected_cross_cluster_restore_0073
        or arguments.business_protected_cross_cluster_restore_0074):
    parser.error("focused protected installer is a standalone test-only run")
if arguments.business_protected_installer_focus and any(
        getattr(arguments, name) for name in vars(arguments)
        if name.endswith("_upgrade") or name in (
            "all_backend_tests", "preprovision_ai_runtime_roles",
            "source_revision_guards_upgrade")):
    parser.error("focused installer cannot combine another migration rehearsal")
if (arguments.protected_archive_layout == "stream-v1"
        and not arguments.business_protected_cross_cluster_restore_0073):
    parser.error("stream-v1 requires the explicit 0073 cross-cluster rehearsal")
if arguments.business_protected_cross_cluster_restore_0074:
    if (arguments.business_protected_cross_cluster_restore
            or arguments.business_protected_cross_cluster_restore_0073
            or arguments.business_protected_migration_role_rehearsal
            or arguments.business_protected_installer_focus
            or arguments.tests_only):
        parser.error("Choose one protected 0074 cross-cluster rehearsal")
    arguments.business_market_human_cap_upgrade = True
if arguments.business_v4_report_restricted_page_upgrade:
    if (arguments.tests_only or arguments.test_label
            or arguments.business_protected_cross_cluster_restore_0074):
        parser.error("0075 upgrade is a standalone isolated rehearsal")
    arguments.business_market_human_cap_upgrade = True
if arguments.business_market_human_cap_upgrade:
    if arguments.business_protected_cross_cluster_restore_0073:
        parser.error("0074 upgrade cannot combine the 0073 cross-cluster rehearsal")
    arguments.business_v11_login_attestation_upgrade = True
if arguments.business_protected_cross_cluster_restore_0073:
    if (arguments.business_protected_cross_cluster_restore
            or arguments.business_protected_migration_role_rehearsal
            or arguments.tests_only):
        parser.error("Choose one protected 0073 cross-cluster rehearsal")
    arguments.business_v11_login_attestation_upgrade = True
if arguments.business_protected_migration_role_rehearsal:
    if arguments.business_protected_cross_cluster_restore:
        parser.error("Choose only one protected rehearsal")
    arguments.business_promotion_budget_v11_stage_upgrade = True
if arguments.business_protected_cross_cluster_restore:
    arguments.business_market_v2_authority_upgrade = True
if arguments.business_v11_login_attestation_upgrade:
    if arguments.business_protected_cross_cluster_restore:
        parser.error("Choose only one protected rehearsal")
    arguments.business_market_v2_authority_upgrade = True
if arguments.preprovision_ai_runtime_roles and (
        not (arguments.tests_only or
            arguments.business_v4_report_restricted_page_focused_upgrade or
            arguments.business_v11_ticket_sign_focused_upgrade or
            arguments.business_market_v6_topology_focused_upgrade or
            arguments.business_v11_publication_focused_upgrade or
            arguments.business_market_v6_source_ticket_focused_upgrade)
        or arguments.upgrade_only):
    parser.error("Preprovisioned AI runtime roles are only for isolated tests")
if arguments.business_v4_report_restricted_reader_login and (
        not arguments.tests_only or not arguments.preprovision_ai_runtime_roles
        or arguments.upgrade_only or not arguments.test_label
        or any(not label.startswith(
            "ai_assistant.test_business_v4_report_restricted_page_role")
            for label in arguments.test_label)):
    parser.error("0075 synthetic reader login requires only its isolated role tests")
if arguments.business_market_v2_authority_upgrade:
    if arguments.business_v4_report_link_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # 0072 requires exact independently restored 0071 report-link predecessor.
    arguments.business_v4_report_link_upgrade = True
if arguments.business_v4_report_link_upgrade:
    if arguments.business_promotion_budget_v11_identity_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # 0071 starts only from an independently restored 0070 identity seed.
    arguments.business_promotion_budget_v11_identity_upgrade = True
if arguments.business_promotion_budget_v11_identity_upgrade:
    if arguments.business_market_v2_paid_round_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # 0070 starts after the independently restored exact 0069 market sidecar.
    arguments.business_market_v2_paid_round_upgrade = True
if arguments.business_market_v2_paid_round_upgrade:
    if arguments.business_promotion_budget_v11_verifier_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # 0069 starts from the independently restored exact 0068 verifier seed.
    arguments.business_promotion_budget_v11_verifier_upgrade = True
if arguments.business_promotion_budget_v11_verifier_upgrade:
    if arguments.business_promotion_budget_v11_attestation_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # 0068 must start from the restored and reapplied exact 0067 proof seed.
    arguments.business_promotion_budget_v11_attestation_upgrade = True
if arguments.business_promotion_budget_v11_attestation_upgrade:
    if arguments.business_promotion_budget_v11_stage_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # 0067 starts from the restored and reapplied exact 0066 stage seed.
    arguments.business_promotion_budget_v11_stage_upgrade = True
if arguments.business_promotion_budget_v11_stage_upgrade:
    if arguments.business_market_v2_cost_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # 0066 starts only from the independently restored 0065 cost seed.
    arguments.business_market_v2_cost_upgrade = True
if arguments.business_market_v2_cost_upgrade:
    if arguments.business_market_v2_synthetic_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # 0065 starts from the independently restored 0064 synthetic seed.
    arguments.business_market_v2_synthetic_upgrade = True
if arguments.business_market_v2_synthetic_upgrade:
    if arguments.business_market_v2_execution_plan_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # 0064 starts from the independently restored closed 0063 plan seed.
    arguments.business_market_v2_execution_plan_upgrade = True
if arguments.business_market_v2_execution_plan_upgrade:
    if arguments.business_market_v2_read_receipt_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # 0063 follows a complete 0062 closed read-receipt seed and restore.
    arguments.business_market_v2_read_receipt_upgrade = True
if arguments.business_market_v2_read_receipt_upgrade:
    if arguments.business_market_v2_context_proof_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # 0062 follows exact 0061 protected context proof and independent restore.
    arguments.business_market_v2_context_proof_upgrade = True
if arguments.business_market_v2_context_proof_upgrade:
    if arguments.business_market_v2_execution_snapshot_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # 0061 starts only after the exact 0060 independent restore.
    arguments.business_market_v2_execution_snapshot_upgrade = True
if arguments.business_market_v2_execution_snapshot_upgrade:
    if arguments.business_promotion_budget_v10_reader_fence_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # 0060 follows exact 0059 reader fence plus its independent restore.
    arguments.business_promotion_budget_v10_reader_fence_upgrade = True
if arguments.business_promotion_budget_v10_reader_fence_upgrade:
    if arguments.business_promotion_budget_v10_publish_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # 0059 follows the complete 0058 publish-gate seed and unchanged old rows.
    arguments.business_promotion_budget_v10_publish_upgrade = True
if arguments.business_promotion_budget_v10_publish_upgrade:
    if arguments.business_promotion_budget_v10_attestation_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # 0058 requires the independently restored closed 0057 attestation seed.
    arguments.business_promotion_budget_v10_attestation_upgrade = True
if arguments.business_promotion_budget_v10_attestation_upgrade:
    if arguments.business_market_v2_role_bridge_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # 0057 follows the complete 0056 role bridge and restored 80-table seed.
    arguments.business_market_v2_role_bridge_upgrade = True
if arguments.business_market_v2_role_bridge_upgrade:
    if arguments.business_v4_period_plan_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # 0056 follows the complete 0055 period sidecar and restored ACL seed.
    arguments.business_v4_period_plan_upgrade = True
if arguments.business_v4_period_plan_upgrade:
    if arguments.business_promotion_budget_v10_stage_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # 0055 must start from the independently restored 0054 stage-only seed.
    arguments.business_promotion_budget_v10_stage_upgrade = True
if arguments.business_promotion_budget_v10_stage_upgrade:
    if arguments.business_market_v2_admitted_paused_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # 0054 requires the independently restored 0053 admitted-paused seed.
    arguments.business_market_v2_admitted_paused_upgrade = True
if arguments.business_market_v2_admitted_paused_upgrade:
    if arguments.business_v4_commit_consumption_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # 0053 starts only from the independently restored 0052 function/catalog seed.
    arguments.business_v4_commit_consumption_upgrade = True
if arguments.business_v4_commit_consumption_upgrade:
    if arguments.business_v4_prior_claim_qualification_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # 0052 installs only after the exact 0051 prior-claim writer seed.
    arguments.business_v4_prior_claim_qualification_upgrade = True
if arguments.business_v4_prior_claim_qualification_upgrade:
    if arguments.business_v4_replay_read_cast_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # The 0051 writer fix requires the exact 0050 read-cast seed.
    arguments.business_v4_replay_read_cast_upgrade = True
if arguments.business_v4_replay_read_cast_upgrade:
    if arguments.business_v4_sealer_source_bridge_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # The 0050 reader fix requires the exact 0049 source bridge seed.
    arguments.business_v4_sealer_source_bridge_upgrade = True
if arguments.business_v4_sealer_source_bridge_upgrade:
    if arguments.business_v4_finance_replay_progress_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    arguments.business_v4_finance_replay_progress_upgrade = True
if arguments.business_v4_finance_replay_progress_upgrade:
    if arguments.business_v4_replay_progress_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    arguments.business_v4_replay_progress_upgrade = True
if arguments.business_v4_replay_progress_upgrade:
    if arguments.business_promotion_trial_file_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # 0047 replays the complete 0046 predecessor before adding the closed ledger.
    arguments.business_promotion_trial_file_upgrade = True
if arguments.business_promotion_trial_file_upgrade:
    if arguments.business_market_v2_material_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # Renderer 9 must rehearse the complete 0045 predecessor first.
    arguments.business_market_v2_material_upgrade = True
if arguments.business_market_v2_material_upgrade:
    if arguments.business_market_v2_parked_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # The 0045 exercise must preserve and verify the entire 0044 lineage.
    arguments.business_market_v2_parked_upgrade = True
if arguments.business_market_v2_parked_upgrade:
    if arguments.business_v4_seal_consumption_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # The 0044 exercise replays the complete 0043 predecessor chain first.
    arguments.business_v4_seal_consumption_upgrade = True
if arguments.business_v4_seal_consumption_upgrade:
    if arguments.business_v4_claimed_read_upgrade:
        parser.error("Choose only one fresh database upgrade rehearsal")
    # The 0043 exercise must replay every frozen predecessor through 0042.
    arguments.business_v4_claimed_read_upgrade = True
if not 60 <= arguments.test_timeout_seconds <= 1800:
    parser.error("--test-timeout-seconds must stay within 60-1800")
if arguments.upgrade_only and (arguments.tests_only or arguments.test_label or arguments.all_backend_tests
        or not any((arguments.generation_upgrade, arguments.prompt_settings_upgrade, arguments.report_library_upgrade, arguments.business_evidence_upgrade, arguments.market_options_upgrade, arguments.sales_options_upgrade, arguments.business_file_opc_upgrade, arguments.business_promotion_profile_upgrade, arguments.business_promotion_file_guard_upgrade, arguments.business_finance_v3_upgrade, arguments.business_promotion_file_ready_upgrade, arguments.business_finance_v3_pages_upgrade, arguments.business_v3_daily_pages_upgrade, arguments.business_v3_tool_receipts_upgrade, arguments.business_v3_parent_seal_upgrade, arguments.business_v3_report_intent_upgrade, arguments.business_v4_ledger_upgrade, arguments.business_v4_validation_upgrade, arguments.business_v4_seal_admission_upgrade, arguments.business_v4_seal_writer_upgrade, arguments.business_v4_sealer_ledger_read_upgrade, arguments.business_v4_sealer_narrow_stream_upgrade, arguments.business_v4_seal_ticket_upgrade, arguments.business_v4_claimed_read_upgrade, arguments.source_revision_guards_upgrade))):
    parser.error("--upgrade-only requires one full upgrade rehearsal and cannot include test-selection options")
if arguments.test_label and (not arguments.tests_only or arguments.generation_upgrade or arguments.prompt_settings_upgrade or arguments.report_library_upgrade or arguments.business_evidence_upgrade or arguments.market_options_upgrade or arguments.sales_options_upgrade or arguments.business_file_opc_upgrade or arguments.business_promotion_profile_upgrade or arguments.business_promotion_file_guard_upgrade or arguments.business_finance_v3_pages_upgrade or arguments.business_v3_daily_pages_upgrade or arguments.business_v3_tool_receipts_upgrade or arguments.business_v3_parent_seal_upgrade or arguments.business_v3_report_intent_upgrade or arguments.business_v4_ledger_upgrade or arguments.business_v4_validation_upgrade or arguments.business_v4_seal_admission_upgrade or arguments.business_v4_seal_writer_upgrade or arguments.business_v4_sealer_ledger_read_upgrade or arguments.business_v4_sealer_narrow_stream_upgrade or arguments.business_v4_seal_ticket_upgrade or arguments.business_v4_claimed_read_upgrade or arguments.source_revision_guards_upgrade):
    parser.error("Explicit test labels require --tests-only and cannot narrow upgrade verification")
if sum([arguments.generation_upgrade, arguments.prompt_settings_upgrade, arguments.report_library_upgrade, arguments.business_evidence_upgrade, arguments.market_options_upgrade, arguments.business_file_opc_upgrade, arguments.business_promotion_profile_upgrade, arguments.business_promotion_file_ready_upgrade, arguments.business_finance_v3_pages_upgrade, arguments.business_v3_daily_pages_upgrade, arguments.business_v3_tool_receipts_upgrade, arguments.business_v3_parent_seal_upgrade, arguments.business_v3_report_intent_upgrade, arguments.business_v4_ledger_upgrade, arguments.business_v4_validation_upgrade, arguments.business_v4_seal_admission_upgrade, arguments.business_v4_seal_writer_upgrade, arguments.business_v4_sealer_ledger_read_upgrade, arguments.business_v4_sealer_narrow_stream_upgrade, arguments.business_v4_seal_ticket_upgrade, arguments.business_v4_claimed_read_upgrade, arguments.source_revision_guards_upgrade]) > 1:
    parser.error("Choose only one fresh database upgrade rehearsal")
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
PORT = arguments.port
if not 55440 <= PORT <= 55999:
    raise RuntimeError("Rehearsal port must stay in the isolated range")
RUN = ROOT / ".runtime" / ("ai-pg-" + secrets.token_hex(6))
RUN.mkdir(parents=True)
RUN = RUN.resolve()
if (
    not RUN.is_relative_to((ROOT / ".runtime").resolve())
    or ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
):
    raise RuntimeError("This rehearsal requires an isolated worktree.")
with socket.socket() as probe:
    probe.bind(("127.0.0.1", PORT))
password = secrets.token_hex(32)
password_file = RUN / "password.txt"
password_file.write_text(password, encoding="ascii")
os.chmod(password_file, 0o600)
environment = {
    **os.environ,
    "PGPASSWORD": password,
    "PGHOST": "127.0.0.1",
    "PGPORT": str(PORT),
    "PGUSER": "ai_rehearsal_admin",
    "PGDATABASE": "postgres",
}


def run(arguments, timeout=300, env=None):
    # pg_ctl's descendant cmd.exe can retain PIPE handles after pg_ctl exits.
    # File handles keep subprocess.wait bounded without waiting for child EOF.
    output = RUN / ("command-" + secrets.token_hex(4) + ".log")
    with output.open("wb") as stream:
        result = subprocess.run(
            [str(v) for v in arguments],
            env=env or environment,
            cwd=ROOT,
            stdout=stream,
            stderr=stream,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    data = output.read_bytes()
    if result.returncode:
        # All subprocess arguments are fixed paths/options. Credentials stay in
        # environment and a private temporary file; no URL is printed.
        (RUN / "failure.log").write_bytes(data)
        raise RuntimeError("Isolated command failed; see " + str(RUN / "failure.log"))
    return data.decode("utf-8", errors="replace")


started = False
try:
    run(
        [
            BIN / "initdb.exe",
            "-D",
            RUN / "data",
            "-U",
            "ai_rehearsal_admin",
            "--auth=scram-sha-256",
            "--encoding=UTF8",
            "--locale=C",
            "--pwfile",
            password_file,
        ]
    )
    with (RUN / "data/postgresql.conf").open("a", encoding="utf-8") as config:
        config.write(
            f"\nlisten_addresses='127.0.0.1'\nport={PORT}\nmax_connections=128\n"
        )
    run(
        [
            BIN / "pg_ctl.exe",
            "-D",
            RUN / "data",
            "-l",
            RUN / "postgres.log",
            "-w",
            "-t",
            "30",
            "start",
        ],
        60,
    )
    started = True
    run([BIN / "createdb.exe", "teruisi_ai_rehearsal"])
    # 0038 deliberately never creates database roles: production must
    # pre-provision this exact, non-login identity through the protected
    # controller. Only this verified isolated cluster gets a synthetic role.
    run([BIN / "psql.exe", "-d", "teruisi_ai_rehearsal", "-v", "ON_ERROR_STOP=1",
        "-c", "CREATE ROLE teruisi_ai_seal_writer NOLOGIN NOINHERIT "
        "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"])
    if arguments.preprovision_ai_runtime_roles:
        for role in ("teruisi_ai_reader", "teruisi_ai_writer"):
            run([BIN / "psql.exe", "-d", "teruisi_ai_rehearsal", "-v", "ON_ERROR_STOP=1",
                "-c", "CREATE ROLE " + role + " LOGIN NOINHERIT "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"])
    synthetic_0075_reader_password = None
    if arguments.business_v4_report_restricted_reader_login:
        import psycopg
        from psycopg import sql
        synthetic_0075_reader_password = secrets.token_hex(32)
        with psycopg.connect(host="127.0.0.1", port=PORT,
                dbname="teruisi_ai_rehearsal", user="ai_rehearsal_admin",
                password=password, autocommit=True) as installer:
            installer.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier("teruisi_ai_reader"),
                sql.Literal(synthetic_0075_reader_password)))
            installer.execute("ALTER ROLE teruisi_ai_reader SET "
                "default_transaction_read_only=on")
    if arguments.business_protected_shadow_snapshot_0073:
        # Only the explicit shadow needs these closed role names for the
        # existing cross-domain backup evidence ACL checks. This precedes
        # migrations; existing roles are verified, never repaired.
        role_env = {**environment, "PGDATABASE": "teruisi_ai_rehearsal"}
        role_output = run([sys.executable, ROOT / "tools" /
            "protected-ai-shadow-role-preflight-0073.py", "--run-root", RUN,
            "--port", str(PORT), "--enabled"], env=role_env)
        role_receipt = json.loads(role_output)
        if (role_receipt.get("status") != "passed"
                or role_receipt.get("identityChecks") != {
                    "database": True, "user": True, "loopback": True,
                    "port": True, "recovery": True}
                or role_receipt.get("evidenceRoles") != 6
                or role_receipt.get("closedNoLoginNoPassword") is not True
                or role_receipt.get("memberships") != 0
                or role_receipt.get("productionWrites") is not False):
            raise RuntimeError("0073 shadow evidence role preflight is incomplete")
        (RUN / "shadow-role-preflight-0073.json").write_text(
            role_output, encoding="utf-8")
    django_env = {
        **environment,
        "DJANGO_SECRET_KEY": secrets.token_hex(32),
        "TERUISI_DJANGO_INTERNAL_SECRET": secrets.token_hex(32),
        "TERUISI_DJANGO_DATABASE_URL": f"postgresql://ai_rehearsal_admin:{quote(password)}@127.0.0.1:{PORT}/teruisi_ai_rehearsal",
        "TERUISI_DJANGO_ENVIRONMENT": "test",
        "TERUISI_DJANGO_PROCESS_ROLE": "development",
        "DJANGO_SETTINGS_MODULE": "teruisi_backend.settings",
        "PYTHONUTF8": "1",
        "TERUISI_AI_REHEARSAL_PORT": str(PORT),
        "TERUISI_AI_REHEARSAL_RUN_ROOT": str(RUN),
    }
    if synthetic_0075_reader_password is not None:
        django_env["TERUISI_AI_0075_SYNTHETIC_READER_PASSWORD"] = (
            synthetic_0075_reader_password)
    print(
        json.dumps(
            {
                "stage": "isolated_cluster_started",
                "port": PORT,
                "productionWrites": False,
            }
        ),
        flush=True,
    )
    if arguments.business_market_v6_source_ticket_focused_upgrade:
        run([sys.executable, ROOT / "backend/manage.py", "migrate",
            "--noinput"], timeout=900, env=django_env)
        run([sys.executable, ROOT / "backend/manage.py", "migrate",
            "ai_assistant", "0078_business_promotion_budget_v11_signed_publication",
            "--noinput"], timeout=180, env=django_env)
        focused = run([sys.executable, ROOT / "tools" /
            "business-market-v6-source-ticket-upgrade-rehearsal.py",
            "--run-root", RUN], timeout=900, env=django_env)
        (RUN / "business-market-v6-source-ticket-focused-upgrade.json"
            ).write_text(focused, encoding="utf-8")
        print(focused.strip(), flush=True)
        sys.exit(0)
    if arguments.business_v11_publication_focused_upgrade:
        run([sys.executable, ROOT / "backend/manage.py", "migrate",
            "--noinput"], timeout=900, env=django_env)
        run([sys.executable, ROOT / "backend/manage.py", "migrate",
            "ai_assistant", "0077_business_market_v6_paused_topology",
            "--noinput"], timeout=180, env=django_env)
        focused = run([sys.executable, ROOT / "tools" /
            "business-v11-publication-upgrade-rehearsal.py",
            "--run-root", RUN], timeout=900, env=django_env)
        (RUN / "business-v11-publication-focused-upgrade.json"
            ).write_text(focused, encoding="utf-8")
        print(focused.strip(), flush=True)
        sys.exit(0)
    if arguments.business_market_v6_topology_focused_upgrade:
        run([sys.executable, ROOT / "backend/manage.py", "migrate",
            "--noinput"], timeout=900, env=django_env)
        run([sys.executable, ROOT / "backend/manage.py", "migrate",
            "ai_assistant", "0076_business_promotion_budget_v11_ticket_bound_signer",
            "--noinput"], timeout=180, env=django_env)
        focused = run([sys.executable, ROOT / "tools" /
            "business-market-v6-paused-topology-upgrade-rehearsal.py",
            "--run-root", RUN], timeout=900, env=django_env)
        (RUN / "business-market-v6-topology-focused-upgrade.json"
            ).write_text(focused, encoding="utf-8")
        print(focused.strip(), flush=True)
        sys.exit(0)
    if arguments.business_v11_ticket_sign_focused_upgrade:
        run([sys.executable, ROOT / "backend/manage.py", "migrate",
            "--noinput"], timeout=900, env=django_env)
        run([sys.executable, ROOT / "backend/manage.py", "migrate",
            "ai_assistant", "0075_business_v4_report_restricted_page",
            "--noinput"], timeout=180, env=django_env)
        focused = run([sys.executable, ROOT / "tools" /
            "business-v11-ticket-sign-upgrade-rehearsal.py",
            "--run-root", RUN], timeout=900, env=django_env)
        (RUN / "business-v11-ticket-sign-focused-upgrade.json"
            ).write_text(focused, encoding="utf-8")
        print(focused.strip(), flush=True)
        sys.exit(0)  # finally stops only this synthetic PostgreSQL cluster.
    if arguments.finance_raw_workbook_upgrade:
        run([sys.executable, ROOT / "backend/manage.py", "migrate",
            "finance", "0005_raw_column_evidence_v2", "--noinput"],
            timeout=900, env=django_env)
        focused = run([sys.executable, ROOT / "tools" /
            "finance-raw-workbook-upgrade-rehearsal.py", "--run-root", RUN],
            timeout=900, env=django_env)
        (RUN / "finance-raw-workbook-upgrade.json").write_text(focused,
            encoding="utf-8")
        print(focused.strip(), flush=True)
        sys.exit(0)
    if arguments.business_v4_report_restricted_page_focused_upgrade:
        run([sys.executable, ROOT / "backend/manage.py", "migrate",
            "--noinput"], timeout=900, env=django_env)
        run([sys.executable, ROOT / "backend/manage.py", "migrate",
            "ai_assistant", "0074_business_market_v2_human_cap_approval",
            "--noinput"], timeout=180, env=django_env)
        focused = run([sys.executable, ROOT / "tools" /
            "business-v4-report-restricted-page-upgrade-rehearsal.py",
            "--run-root", RUN, "--focused-current"], timeout=900,
            env=django_env)
        (RUN / "business-v4-report-restricted-page-focused-upgrade.json"
            ).write_text(focused, encoding="utf-8")
        print(focused.strip(), flush=True)
        sys.exit(0)  # finally stops this exact synthetic cluster.
    if arguments.business_protected_installer_focus:
        focused = run([sys.executable, ROOT / "tools" /
            "protected-ai-installer-focus-rehearsal.py", "--run-root", RUN],
            timeout=900, env=django_env)
        (RUN / "protected-installer-focus.json").write_text(
            focused, encoding="utf-8")
        print(focused.strip(), flush=True)
        sys.exit(0)  # finally stops only this synthetic PostgreSQL cluster.
    if arguments.generation_upgrade:
        upgrade = run([sys.executable, ROOT / "tools/ai-generation-upgrade-rehearsal.py"], env=django_env)
        (RUN / "generation-upgrade.json").write_text(upgrade, encoding="utf-8")
        print(upgrade.strip(), flush=True)
    if arguments.prompt_settings_upgrade:
        upgrade = run([sys.executable, ROOT / "tools/ai-prompt-settings-upgrade-rehearsal.py", "--run-root", RUN], env=django_env)
        (RUN / "prompt-settings-upgrade.json").write_text(upgrade, encoding="utf-8")
        print(upgrade.strip(), flush=True)
    if arguments.report_library_upgrade:
        upgrade = run([sys.executable, ROOT / "tools/ai-report-library-upgrade-rehearsal.py", "--run-root", RUN], env=django_env)
        (RUN / "report-library-upgrade.json").write_text(upgrade, encoding="utf-8")
        print(upgrade.strip(), flush=True)
    if arguments.business_evidence_upgrade:
        upgrade = run([sys.executable, ROOT / "tools/ai-business-evidence-upgrade-rehearsal.py", "--run-root", RUN], env=django_env)
        (RUN / "business-evidence-upgrade.json").write_text(upgrade, encoding="utf-8")
        print(upgrade.strip(), flush=True)
    if arguments.market_options_upgrade:
        upgrade = run([sys.executable, ROOT / "tools/market-options-upgrade-rehearsal.py", "--run-root", RUN], env=django_env)
        (RUN / "market-options-upgrade.json").write_text(upgrade, encoding="utf-8")
        print(upgrade.strip(), flush=True)
    if arguments.sales_options_upgrade:
        upgrade = run([sys.executable, ROOT / "tools/sales-options-upgrade-rehearsal.py", "--run-root", RUN], env=django_env)
        (RUN / "sales-options-upgrade.json").write_text(upgrade, encoding="utf-8")
        print(upgrade.strip(), flush=True)
    if arguments.business_file_opc_upgrade:
        upgrade = run([sys.executable, ROOT / "tools/business-file-opc-upgrade-rehearsal.py", "--run-root", RUN], env=django_env)
        (RUN / "business-file-opc-upgrade.json").write_text(upgrade, encoding="utf-8")
        print(upgrade.strip(), flush=True)
    if arguments.business_promotion_profile_upgrade:
        upgrade = run([sys.executable, ROOT / "tools/business-promotion-profile-upgrade-rehearsal.py", "--run-root", RUN], env=django_env)
        (RUN / "business-promotion-profile-upgrade.json").write_text(upgrade, encoding="utf-8")
        print(upgrade.strip(), flush=True)
    if arguments.business_promotion_file_guard_upgrade:
        predecessor = run([sys.executable, ROOT / "tools/business-promotion-profile-upgrade-rehearsal.py", "--run-root", RUN], env=django_env)
        (RUN / "business-promotion-profile-upgrade.json").write_text(predecessor, encoding="utf-8")
        upgrade = run([sys.executable, ROOT / "tools/business-promotion-file-guard-upgrade-rehearsal.py", "--run-root", RUN], env=django_env)
        (RUN / "business-promotion-file-guard-upgrade.json").write_text(upgrade, encoding="utf-8")
        print(upgrade.strip(), flush=True)
    if arguments.business_finance_v3_upgrade:
        upgrade = run([sys.executable, ROOT / "tools/business-finance-v3-upgrade-rehearsal.py", "--run-root", RUN], env=django_env)
        (RUN / "business-finance-v3-upgrade.json").write_text(upgrade, encoding="utf-8")
        print(upgrade.strip(), flush=True)
    if arguments.business_promotion_file_ready_upgrade:
        predecessor = run([sys.executable, ROOT / "tools/business-promotion-profile-upgrade-rehearsal.py", "--run-root", RUN], env=django_env)
        (RUN / "business-promotion-profile-upgrade.json").write_text(predecessor, encoding="utf-8")
        predecessor = run([sys.executable, ROOT / "tools/business-promotion-file-guard-upgrade-rehearsal.py", "--run-root", RUN], env=django_env)
        (RUN / "business-promotion-file-guard-upgrade.json").write_text(predecessor, encoding="utf-8")
        upgrade = run([sys.executable, ROOT / "tools/business-promotion-file-ready-upgrade-rehearsal.py", "--run-root", RUN], env=django_env)
        (RUN / "business-promotion-file-ready-upgrade.json").write_text(upgrade, encoding="utf-8")
        print(upgrade.strip(), flush=True)
    if arguments.business_finance_v3_pages_upgrade:
        for script, name in (
            ("business-promotion-profile-upgrade-rehearsal.py", "business-promotion-profile-upgrade.json"),
            ("business-promotion-file-guard-upgrade-rehearsal.py", "business-promotion-file-guard-upgrade.json"),
            ("business-promotion-file-ready-upgrade-rehearsal.py", "business-promotion-file-ready-upgrade.json"),
            ("business-finance-v3-pages-upgrade-rehearsal.py", "business-finance-v3-pages-upgrade.json"),
        ):
            upgrade = run([sys.executable, ROOT / "tools" / script, "--run-root", RUN], env=django_env)
            (RUN / name).write_text(upgrade, encoding="utf-8")
        print(upgrade.strip(), flush=True)
    if arguments.business_v3_daily_pages_upgrade:
        for script, name in (
            ("business-promotion-profile-upgrade-rehearsal.py", "business-promotion-profile-upgrade.json"),
            ("business-promotion-file-guard-upgrade-rehearsal.py", "business-promotion-file-guard-upgrade.json"),
            ("business-promotion-file-ready-upgrade-rehearsal.py", "business-promotion-file-ready-upgrade.json"),
            ("business-finance-v3-pages-upgrade-rehearsal.py", "business-finance-v3-pages-upgrade.json"),
            ("business-v3-daily-pages-upgrade-rehearsal.py", "business-v3-daily-pages-upgrade.json"),
        ):
            upgrade = run([sys.executable, ROOT / "tools" / script, "--run-root", RUN], env=django_env)
            (RUN / name).write_text(upgrade, encoding="utf-8")
        print(upgrade.strip(), flush=True)
    if arguments.business_v3_tool_receipts_upgrade:
        for script, name in (
            ("business-promotion-profile-upgrade-rehearsal.py", "business-promotion-profile-upgrade.json"),
            ("business-promotion-file-guard-upgrade-rehearsal.py", "business-promotion-file-guard-upgrade.json"),
            ("business-promotion-file-ready-upgrade-rehearsal.py", "business-promotion-file-ready-upgrade.json"),
            ("business-finance-v3-pages-upgrade-rehearsal.py", "business-finance-v3-pages-upgrade.json"),
            ("business-v3-daily-pages-upgrade-rehearsal.py", "business-v3-daily-pages-upgrade.json"),
            ("business-v3-tool-receipts-upgrade-rehearsal.py", "business-v3-tool-receipts-upgrade.json"),
        ):
            upgrade = run([sys.executable, ROOT / "tools" / script, "--run-root", RUN], env=django_env)
            (RUN / name).write_text(upgrade, encoding="utf-8")
        print(upgrade.strip(), flush=True)
    if arguments.business_v3_parent_seal_upgrade:
        for script, name in (
            ("business-promotion-profile-upgrade-rehearsal.py", "business-promotion-profile-upgrade.json"),
            ("business-promotion-file-guard-upgrade-rehearsal.py", "business-promotion-file-guard-upgrade.json"),
            ("business-promotion-file-ready-upgrade-rehearsal.py", "business-promotion-file-ready-upgrade.json"),
            ("business-finance-v3-pages-upgrade-rehearsal.py", "business-finance-v3-pages-upgrade.json"),
            ("business-v3-daily-pages-upgrade-rehearsal.py", "business-v3-daily-pages-upgrade.json"),
            ("business-v3-tool-receipts-upgrade-rehearsal.py", "business-v3-tool-receipts-upgrade.json"),
            ("business-v3-parent-seal-upgrade-rehearsal.py", "business-v3-parent-seal-upgrade.json"),
        ):
            upgrade = run([sys.executable, ROOT / "tools" / script, "--run-root", RUN], env=django_env)
            (RUN / name).write_text(upgrade, encoding="utf-8")
        print(upgrade.strip(), flush=True)
    if arguments.business_v3_report_intent_upgrade:
        for script, name in (
            ("business-promotion-profile-upgrade-rehearsal.py", "business-promotion-profile-upgrade.json"),
            ("business-promotion-file-guard-upgrade-rehearsal.py", "business-promotion-file-guard-upgrade.json"),
            ("business-promotion-file-ready-upgrade-rehearsal.py", "business-promotion-file-ready-upgrade.json"),
            ("business-finance-v3-pages-upgrade-rehearsal.py", "business-finance-v3-pages-upgrade.json"),
            ("business-v3-daily-pages-upgrade-rehearsal.py", "business-v3-daily-pages-upgrade.json"),
            ("business-v3-tool-receipts-upgrade-rehearsal.py", "business-v3-tool-receipts-upgrade.json"),
            ("business-v3-parent-seal-upgrade-rehearsal.py", "business-v3-parent-seal-upgrade.json"),
            ("business-v3-report-intent-upgrade-rehearsal.py", "business-v3-report-intent-upgrade.json"),
        ):
            upgrade = run([sys.executable, ROOT / "tools" / script, "--run-root", RUN], env=django_env)
            (RUN / name).write_text(upgrade, encoding="utf-8")
        print(upgrade.strip(), flush=True)
    if arguments.business_v4_ledger_upgrade:
        for script, name in (
            ("business-promotion-profile-upgrade-rehearsal.py", "business-promotion-profile-upgrade.json"),
            ("business-promotion-file-guard-upgrade-rehearsal.py", "business-promotion-file-guard-upgrade.json"),
            ("business-promotion-file-ready-upgrade-rehearsal.py", "business-promotion-file-ready-upgrade.json"),
            ("business-finance-v3-pages-upgrade-rehearsal.py", "business-finance-v3-pages-upgrade.json"),
            ("business-v3-daily-pages-upgrade-rehearsal.py", "business-v3-daily-pages-upgrade.json"),
            ("business-v3-tool-receipts-upgrade-rehearsal.py", "business-v3-tool-receipts-upgrade.json"),
            ("business-v3-parent-seal-upgrade-rehearsal.py", "business-v3-parent-seal-upgrade.json"),
            ("business-v3-report-intent-upgrade-rehearsal.py", "business-v3-report-intent-upgrade.json"),
            ("business-v4-ledger-upgrade-rehearsal.py", "business-v4-ledger-upgrade.json"),
        ):
            upgrade = run([sys.executable, ROOT / "tools" / script, "--run-root", RUN], env=django_env)
            (RUN / name).write_text(upgrade, encoding="utf-8")
        print(upgrade.strip(), flush=True)
    if arguments.business_v4_validation_upgrade:
        for script, name in (
            ("business-promotion-profile-upgrade-rehearsal.py", "business-promotion-profile-upgrade.json"),
            ("business-promotion-file-guard-upgrade-rehearsal.py", "business-promotion-file-guard-upgrade.json"),
            ("business-promotion-file-ready-upgrade-rehearsal.py", "business-promotion-file-ready-upgrade.json"),
            ("business-finance-v3-pages-upgrade-rehearsal.py", "business-finance-v3-pages-upgrade.json"),
            ("business-v3-daily-pages-upgrade-rehearsal.py", "business-v3-daily-pages-upgrade.json"),
            ("business-v3-tool-receipts-upgrade-rehearsal.py", "business-v3-tool-receipts-upgrade.json"),
            ("business-v3-parent-seal-upgrade-rehearsal.py", "business-v3-parent-seal-upgrade.json"),
            ("business-v3-report-intent-upgrade-rehearsal.py", "business-v3-report-intent-upgrade.json"),
            ("business-v4-ledger-upgrade-rehearsal.py", "business-v4-ledger-upgrade.json"),
            ("business-v4-validation-upgrade-rehearsal.py", "business-v4-validation-upgrade.json"),
        ):
            upgrade = run([sys.executable, ROOT / "tools" / script, "--run-root", RUN], env=django_env)
            (RUN / name).write_text(upgrade, encoding="utf-8")
        print(upgrade.strip(), flush=True)
    if (arguments.business_v4_seal_admission_upgrade or
            arguments.business_v4_seal_writer_upgrade or
            arguments.business_v4_sealer_ledger_read_upgrade or
            arguments.business_v4_sealer_narrow_stream_upgrade or
            arguments.business_v4_seal_ticket_upgrade or
            arguments.business_v4_claimed_read_upgrade):
        rehearsals = (
            ("business-promotion-profile-upgrade-rehearsal.py", "business-promotion-profile-upgrade.json"),
            ("business-promotion-file-guard-upgrade-rehearsal.py", "business-promotion-file-guard-upgrade.json"),
            ("business-promotion-file-ready-upgrade-rehearsal.py", "business-promotion-file-ready-upgrade.json"),
            ("business-finance-v3-pages-upgrade-rehearsal.py", "business-finance-v3-pages-upgrade.json"),
            ("business-v3-daily-pages-upgrade-rehearsal.py", "business-v3-daily-pages-upgrade.json"),
            ("business-v3-tool-receipts-upgrade-rehearsal.py", "business-v3-tool-receipts-upgrade.json"),
            ("business-v3-parent-seal-upgrade-rehearsal.py", "business-v3-parent-seal-upgrade.json"),
            ("business-v3-report-intent-upgrade-rehearsal.py", "business-v3-report-intent-upgrade.json"),
            ("business-v4-seal-admission-upgrade-rehearsal.py", "business-v4-seal-admission-upgrade.json"),
        )
        if (arguments.business_v4_seal_writer_upgrade or
                arguments.business_v4_sealer_ledger_read_upgrade or
                arguments.business_v4_sealer_narrow_stream_upgrade or
                arguments.business_v4_seal_ticket_upgrade or
                arguments.business_v4_claimed_read_upgrade):
            rehearsals += (("business-v4-seal-writer-upgrade-rehearsal.py",
                "business-v4-seal-writer-upgrade.json"),)
        if (arguments.business_v4_sealer_ledger_read_upgrade or
                arguments.business_v4_sealer_narrow_stream_upgrade or
                arguments.business_v4_seal_ticket_upgrade or
                arguments.business_v4_claimed_read_upgrade):
            rehearsals += (("business-v4-sealer-ledger-read-upgrade-rehearsal.py",
                "business-v4-sealer-ledger-read-upgrade.json"),)
        if (arguments.business_v4_sealer_narrow_stream_upgrade or
                arguments.business_v4_seal_ticket_upgrade or
                arguments.business_v4_claimed_read_upgrade):
            rehearsals += (("business-v4-sealer-narrow-stream-upgrade-rehearsal.py",
                "business-v4-sealer-narrow-stream-upgrade.json"),)
        if arguments.business_v4_seal_ticket_upgrade or arguments.business_v4_claimed_read_upgrade:
            rehearsals += (("business-v4-seal-ticket-upgrade-rehearsal.py",
                "business-v4-seal-ticket-upgrade.json"),)
        if arguments.business_v4_claimed_read_upgrade:
            rehearsals += (("business-v4-claimed-read-upgrade-rehearsal.py",
                "business-v4-claimed-read-upgrade.json"),)
        if arguments.business_v4_seal_consumption_upgrade:
            rehearsals += (("business-v4-seal-consumption-upgrade-rehearsal.py",
                "business-v4-seal-consumption-upgrade.json"),)
        if arguments.business_market_v2_parked_upgrade:
            rehearsals += (("business-market-v2-parked-upgrade-rehearsal.py",
                "business-market-v2-parked-upgrade.json"),)
        if arguments.business_market_v2_material_upgrade:
            rehearsals += (("business-market-v2-material-upgrade-rehearsal.py",
                "business-market-v2-material-upgrade.json"),)
        if arguments.business_promotion_trial_file_upgrade:
            rehearsals += (("business-promotion-trial-file-upgrade-rehearsal.py",
                "business-promotion-trial-file-upgrade.json"),)
        if arguments.business_v4_replay_progress_upgrade:
            rehearsals += (("business-v4-replay-progress-upgrade-rehearsal.py",
                "business-v4-replay-progress-upgrade.json"),)
        if arguments.business_v4_finance_replay_progress_upgrade:
            rehearsals += (("business-v4-finance-replay-progress-upgrade-rehearsal.py",
                "business-v4-finance-replay-progress-upgrade.json"),)
        if arguments.business_v4_sealer_source_bridge_upgrade:
            rehearsals += (("business-v4-sealer-source-bridge-upgrade-rehearsal.py",
                "business-v4-sealer-source-bridge-upgrade.json"),)
        if arguments.business_v4_replay_read_cast_upgrade:
            rehearsals += (("business-v4-replay-read-cast-upgrade-rehearsal.py",
                "business-v4-replay-read-cast-upgrade.json"),)
        if arguments.business_v4_prior_claim_qualification_upgrade:
            rehearsals += (("business-v4-prior-claim-qualification-upgrade-rehearsal.py",
                "business-v4-prior-claim-qualification-upgrade.json"),)
        if arguments.business_v4_commit_consumption_upgrade:
            rehearsals += (("business-v4-commit-consumption-upgrade-rehearsal.py",
                "business-v4-commit-consumption-upgrade.json"),)
        if arguments.business_market_v2_admitted_paused_upgrade:
            rehearsals += (("business-market-v2-admitted-paused-upgrade-rehearsal.py",
                "business-market-v2-admitted-paused-upgrade.json"),)
        if arguments.business_promotion_budget_v10_stage_upgrade:
            rehearsals += (("business-promotion-budget-v10-stage-upgrade-rehearsal.py",
                "business-promotion-budget-v10-stage-upgrade.json"),)
        if arguments.business_v4_period_plan_upgrade:
            rehearsals += (("business-v4-period-plan-upgrade-rehearsal.py",
                "business-v4-period-plan-upgrade.json"),)
        if arguments.business_market_v2_role_bridge_upgrade:
            rehearsals += (("business-market-v2-role-bridge-upgrade-rehearsal.py",
                "business-market-v2-role-bridge-upgrade.json"),)
        if arguments.business_promotion_budget_v10_attestation_upgrade:
            rehearsals += (("business-promotion-budget-v10-attestation-upgrade-rehearsal.py",
                "business-promotion-budget-v10-attestation-upgrade.json"),)
        if arguments.business_promotion_budget_v10_publish_upgrade:
            rehearsals += (("business-promotion-budget-v10-publish-upgrade-rehearsal.py",
                "business-promotion-budget-v10-publish-upgrade.json"),)
        if arguments.business_promotion_budget_v10_reader_fence_upgrade:
            rehearsals += (("business-promotion-budget-v10-reader-fence-upgrade-rehearsal.py",
                "business-promotion-budget-v10-reader-fence-upgrade.json"),)
        if arguments.business_market_v2_execution_snapshot_upgrade:
            rehearsals += (("business-market-v2-execution-snapshot-upgrade-rehearsal.py",
                "business-market-v2-execution-snapshot-upgrade.json"),)
        if arguments.business_market_v2_context_proof_upgrade:
            rehearsals += (("business-market-v2-context-proof-upgrade-rehearsal.py",
                "business-market-v2-context-proof-upgrade.json"),)
        if arguments.business_market_v2_read_receipt_upgrade:
            rehearsals += (("business-market-v2-read-receipt-upgrade-rehearsal.py",
                "business-market-v2-read-receipt-upgrade.json"),)
        if arguments.business_market_v2_execution_plan_upgrade:
            rehearsals += (("business-market-v2-execution-plan-upgrade-rehearsal.py",
                "business-market-v2-execution-plan-upgrade.json"),)
        if arguments.business_market_v2_synthetic_upgrade:
            rehearsals += (("business-market-v2-synthetic-upgrade-rehearsal.py",
                "business-market-v2-synthetic-upgrade.json"),)
        if arguments.business_market_v2_cost_upgrade:
            rehearsals += (("business-market-v2-cost-upgrade-rehearsal.py",
                "business-market-v2-cost-upgrade.json"),)
        if arguments.business_promotion_budget_v11_stage_upgrade:
            rehearsals += (("business-promotion-budget-v11-stage-upgrade-rehearsal.py",
                "business-promotion-budget-v11-stage-upgrade.json"),)
        if arguments.business_promotion_budget_v11_attestation_upgrade:
            rehearsals += (("business-promotion-budget-v11-attestation-upgrade-rehearsal.py",
                "business-promotion-budget-v11-attestation-upgrade.json"),)
        if arguments.business_promotion_budget_v11_verifier_upgrade:
            rehearsals += (("business-promotion-budget-v11-verifier-upgrade-rehearsal.py",
                "business-promotion-budget-v11-verifier-upgrade.json"),)
        if arguments.business_market_v2_paid_round_upgrade:
            rehearsals += (("business-market-v2-paid-round-upgrade-rehearsal.py",
                "business-market-v2-paid-round-upgrade.json"),)
        if arguments.business_promotion_budget_v11_identity_upgrade:
            rehearsals += (("business-promotion-budget-v11-identity-upgrade-rehearsal.py",
                "business-promotion-budget-v11-identity-upgrade.json"),)
        if arguments.business_v4_report_link_upgrade:
            rehearsals += (("business-v4-report-link-upgrade-rehearsal.py",
                "business-v4-report-link-upgrade.json"),)
        if arguments.business_market_v2_authority_upgrade:
            rehearsals += (("business-market-v2-authority-upgrade-rehearsal.py",
                "business-market-v2-authority-upgrade.json"),)
        if arguments.business_v11_login_attestation_upgrade:
            rehearsals += (("business-v11-login-attestation-upgrade-rehearsal.py",
                "business-v11-login-attestation-upgrade.json"),)
        if arguments.business_market_human_cap_upgrade:
            rehearsals += (("business-market-v2-human-cap-upgrade-rehearsal.py",
                "business-market-v2-human-cap-upgrade.json"),)
        if arguments.business_v4_report_restricted_page_upgrade:
            rehearsals += (("business-v4-report-restricted-page-upgrade-rehearsal.py",
                "business-v4-report-restricted-page-upgrade.json"),)
        for script, name in rehearsals:
            upgrade = run([sys.executable, ROOT / "tools" / script, "--run-root", RUN], env=django_env)
            (RUN / name).write_text(upgrade, encoding="utf-8")
        print(upgrade.strip(), flush=True)
        if arguments.business_protected_shadow_snapshot_0073:
            seed_path = RUN / "business-v11-login-attestation-upgrade-evidence.json"
            seed = json.loads(seed_path.read_text(encoding="utf-8"))
            if (seed.get("upgrade") != "0072->0073"
                    or seed.get("oldFileChunkCount") != 7
                    or seed.get("productionWrites") is not False):
                raise RuntimeError("frozen 0073 seven-chunk seed is unavailable")
            run([sys.executable, ROOT / "tools" /
                "protected-ai-shadow-snapshot-0073.py", "--run-root", RUN,
                "--target-port", str(arguments.shadow_target_port),
                "--enabled"], timeout=1800, env=django_env)
            shadow_file = RUN / "shadow-snapshot-0073/evidence.json"
            shadow = json.loads(shadow_file.read_text(encoding="utf-8"))
            if (shadow.get("status") != "passed"
                    or shadow.get("fileChunkCount") != 7
                    or not {"html", "xlsx"} <= set(shadow.get("fileFormats", []))
                    or shadow.get("syntheticVerifierKeyRows") != 1
                    or shadow.get("ownerAclAndFilesRestored") is not True
                    or shadow.get("formalBackupPathVerified") is not False
                    or shadow.get("longTermRestorePossible") is not False
                    or shadow.get("productionWrites") is not False):
                raise RuntimeError("frozen 0073 shadow evidence is incomplete")
            print(json.dumps(shadow, ensure_ascii=False), flush=True)
        if arguments.business_protected_cross_cluster_restore:
            restored = run([sys.executable, ROOT / "tools" /
                "business-protected-cross-cluster-restore-rehearsal.py",
                "--run-root", RUN], timeout=600, env=django_env)
            (RUN / "business-protected-cross-cluster-restore.json").write_text(
                restored, encoding="utf-8")
            print(restored.strip(), flush=True)
        if arguments.business_protected_cross_cluster_restore_0073:
            restored = run([sys.executable, ROOT / "tools" /
                "business-protected-cross-cluster-restore-rehearsal.py",
                "--run-root", RUN, "--generation", "0073",
                "--archive-layout", arguments.protected_archive_layout], timeout=600,
                env=django_env)
            (RUN / "business-protected-cross-cluster-restore-0073.json").write_text(
                restored, encoding="utf-8")
            print(restored.strip(), flush=True)
        if arguments.business_protected_cross_cluster_restore_0074:
            restored = run([sys.executable, ROOT / "tools" /
                "business-protected-cross-cluster-restore-rehearsal.py",
                "--run-root", RUN, "--generation", "0074"], timeout=600,
                env=django_env)
            (RUN / "business-protected-cross-cluster-restore-0074.json").write_text(
                restored, encoding="utf-8")
            print(restored.strip(), flush=True)
        if arguments.business_protected_migration_role_rehearsal:
            role_result = run([sys.executable, ROOT / "tools" /
                "business-protected-migration-role-rehearsal.py",
                "--run-root", RUN], timeout=600, env=django_env)
            (RUN / "business-protected-migration-role-rehearsal.json").write_text(
                role_result, encoding="utf-8")
            print(role_result.strip(), flush=True)
    if arguments.source_revision_guards_upgrade:
        upgrade = run([sys.executable, ROOT / "tools" /
            "source-revision-guards-upgrade-rehearsal.py", "--run-root", RUN],
            env=django_env)
        (RUN / "source-revision-guards-upgrade.json").write_text(
            upgrade, encoding="utf-8")
        print(upgrade.strip(), flush=True)
    if arguments.upgrade_only:
        print(json.dumps({"status":"passed", "mode":"upgrade-only", "testSuitesRun":False,
            "runRoot":str(RUN), "productionWrites":False}),flush=True)
        sys.exit(0)  # The same finally stops only this isolated cluster.
    tests = run(
        [
            sys.executable,
            ROOT / "backend/manage.py",
            "test",
            *(arguments.test_label or ["ai_assistant"]),
            *(["system_datasets"] if arguments.prompt_settings_upgrade or arguments.report_library_upgrade else []),
            "--noinput",
            "--verbosity",
            str(arguments.test_verbosity),
        ],
        timeout=arguments.test_timeout_seconds,
        env=django_env,
    )
    (RUN / "tests.log").write_text(tests, encoding="utf-8")
    if arguments.tests_only:
        print(json.dumps({"status": "passed", "mode": "tests-only", "tests": str(RUN / "tests.log"), "productionWrites": False}), flush=True)
        sys.exit(0)  # finally still stops this exact isolated cluster.
    if arguments.all_backend_tests:
        # Existing domain unit suites include SQLite-specific fixtures. Exercise
        # their supported unit environment separately from AI's PostgreSQL gates.
        unit_env = {
            **django_env,
            "TERUISI_DJANGO_SQLITE_PATH": str(RUN / "backend-unit.sqlite3"),
        }
        unit_env.pop("TERUISI_DJANGO_DATABASE_URL", None)
        unit_tests = run(
            [
                sys.executable,
                ROOT / "backend/manage.py",
                "test",
                "ai_assistant",
                "access_control",
                "sales",
                "erp_reference",
                "finance",
                "netshop",
                "market",
                "products",
                "inventory",
                "workflow",
                "customer_service",
                "bi",
                "teruisi_backend",
                "--noinput",
                "--verbosity",
                "1",
            ],
            env=unit_env,
        )
        (RUN / "backend-regression.log").write_text(unit_tests, encoding="utf-8")
    run(
        [
            sys.executable,
            ROOT / "backend/manage.py",
            "migrate",
            "--noinput",
            "--verbosity",
            "0",
        ],
        env=django_env,
    )
    source = ROOT / ".runtime/ai-source-rehearsal.sqlite"
    dry = json.loads(
        run(
            [
                sys.executable,
                ROOT / "backend/manage.py",
                "migrate_ai_from_d1",
                "--source",
                source,
                "--mode",
                "dry-run",
            ],
            env=django_env,
        )
    )
    applied = json.loads(
        run(
            [
                sys.executable,
                ROOT / "backend/manage.py",
                "migrate_ai_from_d1",
                "--source",
                source,
                "--mode",
                "apply",
                "--approve-run-id",
                dry["runId"],
            ],
            env=django_env,
        )
    )
    verified = json.loads(
        run(
            [
                sys.executable,
                ROOT / "backend/manage.py",
                "migrate_ai_from_d1",
                "--source",
                source,
                "--mode",
                "verify-only",
            ],
            env=django_env,
        )
    )
    # Restore the complete isolated database before runtime traffic can change it.
    archive = RUN / "isolated-preactivation.dump"
    run(
        [
            BIN / "pg_dump.exe",
            "--format=custom",
            "--file",
            archive,
            "teruisi_ai_rehearsal",
        ]
    )
    run([BIN / "createdb.exe", "teruisi_ai_restore"])
    run(
        [
            BIN / "pg_restore.exe",
            "--exit-on-error",
            "--dbname",
            "teruisi_ai_restore",
            archive,
        ]
    )
    restore_env = {
        **django_env,
        "TERUISI_DJANGO_DATABASE_URL": django_env[
            "TERUISI_DJANGO_DATABASE_URL"
        ].replace("/teruisi_ai_rehearsal", "/teruisi_ai_restore"),
    }
    restored = json.loads(
        run(
            [
                sys.executable,
                ROOT / "backend/manage.py",
                "migrate_ai_from_d1",
                "--source",
                source,
                "--mode",
                "verify-only",
            ],
            env=restore_env,
        )
    )
    if restored["targetDigest"] != verified["targetDigest"]:
        raise RuntimeError("Isolated backup restoration digest mismatch")
    run(
        [
            sys.executable,
            ROOT / "tools/ai-runtime-rehearsal.py",
            "--run-root",
            RUN,
            "--apply-run",
            applied["runId"],
        ],
        env=django_env,
    )
    # A post-activation archive must retain the terminal authority and runtime
    # mutations too; a pre-activation restore alone cannot prove PNR recovery.
    run(
        [
            BIN / "pg_dump.exe",
            "--format=custom",
            "--file",
            RUN / "isolated-postactivation.dump",
            "teruisi_ai_rehearsal",
        ]
    )
    run([BIN / "createdb.exe", "teruisi_ai_terminal_restore"])
    run(
        [
            BIN / "pg_restore.exe",
            "--exit-on-error",
            "--dbname",
            "teruisi_ai_terminal_restore",
            RUN / "isolated-postactivation.dump",
        ]
    )
    import hashlib
    import psycopg

    sys.path.insert(0, str(ROOT / "backend"))
    from ai_assistant.table_manifest import AI_TABLES

    def restored_tables(database):
        with psycopg.connect(
            django_env["TERUISI_DJANGO_DATABASE_URL"].replace(
                "/teruisi_ai_rehearsal", "/" + database
            )
        ) as restored_connection:
            evidence = {}
            for table in AI_TABLES:
                rows = restored_connection.execute(
                    f'SELECT row_to_json(t)::text FROM "{table}" t'
                ).fetchall()
                normalized = sorted(
                    json.dumps(
                        json.loads(row[0]), sort_keys=True, separators=(",", ":")
                    )
                    for row in rows
                )
                evidence[table] = hashlib.sha256(
                    "\n".join(normalized).encode()
                ).hexdigest()
            status = restored_connection.execute(
                "SELECT status FROM ai_write_authority WHERE id=1"
            ).fetchone()[0]
            if status != "postgres":
                raise RuntimeError("Restored AI terminal authority is missing")
            return evidence

    terminal = restored_tables("teruisi_ai_rehearsal")
    if restored_tables("teruisi_ai_terminal_restore") != terminal:
        raise RuntimeError("Post-activation AI restore differs from the source")
    report = {
        "status": "passed",
        "port": PORT,
        "productionWrites": False,
        "dryRun": dry,
        "apply": applied,
        "verify": verified,
        "restored": restored,
        "terminalRestore": {
            "status": "passed",
            "tables": len(AI_TABLES),
            "authority": "postgres",
            "productionDatabaseTouched": False,
        },
        "system": json.loads((RUN / "system-result.json").read_text(encoding="utf-8")),
    }
    (RUN / "result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "passed",
                "sourceDigest": verified["sourceDigest"],
                "targetDigest": verified["targetDigest"],
                "totalRows": sum(verified["counts"].values()),
                "result": str(RUN / "result.json"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
finally:
    if started:
        run(
            [
                BIN / "pg_ctl.exe",
                "-D",
                RUN / "data",
                "-m",
                "fast",
                "-w",
                "-t",
                "240",
                "stop",
            ],
            300,
        )
    password_file.unlink(missing_ok=True)

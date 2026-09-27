"""Default-closed 0067-0073 protected migration installer contract.

Only a synthetic, loopback rehearsal may use this module. It does not read a
production credential, install a service, or connect to the formal database.
Its injected executor is deliberately separate from the formal Django Start,
PrepareApp, DeployApp, backup and restore entry points. A failed or uncertain
step is never replayed in the same operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from typing import Mapping, Protocol


ROOT = Path(__file__).resolve().parents[1]
BASELINE = "0066_business_promotion_budget_v11_durable_stage"
CLONE_DATABASE = "teruisi_ai_migration_role_probe"
ADMIN_ROLE = "ai_rehearsal_admin"
ORDINARY_ROLE = "teruisi_sales_owner"
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class Step:
    name: str
    installer: str  # ordinary or privileged; never a Web/backup identity.
    roles: tuple[str, ...]


STEPS = (
    Step("0067_business_promotion_budget_v11_attestation", "ordinary",
        ("teruisi_ai_budget_v11_attestor",)),
    Step("0068_business_promotion_budget_v11_verifier_receipt", "privileged",
        ("teruisi_ai_budget_v11_key_owner",
         "teruisi_ai_budget_v11_publisher")),
    Step("0069_business_market_v2_paid_round_rehearsal", "ordinary",
        ("teruisi_ai_market_paid_adopter",
         "teruisi_ai_market_paid_reserver",
         "teruisi_ai_market_paid_starter")),
    Step("0070_business_promotion_budget_v11_limited_identity", "privileged",
        ("teruisi_ai_budget_v11_attest_login",
         "teruisi_ai_budget_v11_sign_login",
         "teruisi_ai_budget_v11_publish_login")),
    Step("0071_business_v4_report_source_link", "ordinary", ()),
    Step("0072_business_market_v2_authority_proposals", "ordinary",
        ("teruisi_ai_market_rate_proposer",
         "teruisi_ai_market_cap_proposer",
         "teruisi_ai_market_proposal_revoker")),
    Step("0073_business_promotion_budget_v11_login_attestation", "privileged",
        ("teruisi_ai_budget_v11_attestor_v2_login",)),
)
STEP_NAMES = tuple(step.name for step in STEPS)
ROLE_NAMES = frozenset(role for step in STEPS for role in step.roles)

# Reviewed on source commit de7ed95e. A changed migration or its directly
# referenced SQL module requires an explicit code review and new pinned hash.
PINNED_SOURCE_SHA256 = {
    "backend/manage.py":
        "aacdf80bb3599ccbb90e4fdcf471f7fc38352e3083ce7f447f714e5f1caaeb25",
    "backend/teruisi_backend/settings.py":
        "d92c7fe6e5251c65a2c80d8d62f94fdcab64696ac75421395aba6e44e7c9ba33",
    "backend/ai_assistant/migrations/0066_business_promotion_budget_v11_durable_stage.py":
        "cc7af8967153a02fdf5a7185359fdeaf7456ba065570484a356790c4877c7cfb",
    "backend/ai_assistant/migrations/0067_business_promotion_budget_v11_attestation.py":
        "64b6c4f8481f0fe41832aa75b7fdb84a36504d37c448d018ca2ee8128aa9447a",
    "backend/ai_assistant/migrations/0068_business_promotion_budget_v11_verifier_receipt.py":
        "c299237d9c2534f9a9097a5cf61875a360af526e6c765fce86a9f0c27cee628b",
    "backend/ai_assistant/migrations/0069_business_market_v2_paid_round_rehearsal.py":
        "f47f094c7b3cb3ed187ceb8b8c49eac1b0a5d741dcd35ec043470929626b24e6",
    "backend/ai_assistant/migrations/0070_business_promotion_budget_v11_limited_identity.py":
        "9931668c379ab94d5ebedff1873a03117952c72e687fd0a8816871ed6570507f",
    "backend/ai_assistant/migrations/0071_business_v4_report_source_link.py":
        "f7ca65220de43f4490da9c0d0968458b7629eb627c8dcc7739b2942be64880b2",
    "backend/ai_assistant/migrations/0072_business_market_v2_authority_proposals.py":
        "039c57d3e8830be14cc32bd7ca7f08e2677f7bef0911e69f538fd834f9249cd7",
    "backend/ai_assistant/migrations/0073_business_promotion_budget_v11_login_attestation.py":
        "7d6d1e457e4ab05c3afdc111ceb6795a7735f27c0fc798bde9027bbf97fe35b3",
    "backend/ai_assistant/business_promotion_budget_v11_stage_sql.py":
        "eee5be413fe183837fcf13ab7460e5e37e4c677c2b1d11a5588c524ca2233c7d",
    "backend/ai_assistant/business_promotion_budget_v11_identity_candidate_sql.py":
        "ce2ee30ecbdbaa125fb444ded0e11d1144b7dc9a9cbc22dcb1753d01287342b1",
    "backend/ai_assistant/business_v4_report_link_sql.py":
        "548b2b87c627b5c6a9fbca0582ebc346d2ad2ff608c34f86b6b4d00438452601",
    "backend/ai_assistant/business_market_v2_authority_sql.py":
        "c19f3ed7d667d5284a3b57ad89586f8cc1756ffec852bb1aa1c58220b68926f2",
    "backend/ai_assistant/business_promotion_budget_v11_login_attestation_sql.py":
        "76162532aefc0cacc0b16f3a710a2bf1008d508e369fafe1abc99b5577c84a18",
}


class InstallerBlocked(RuntimeError):
    """A protected migration state is outside the approved isolated contract."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("ascii")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _plan_digest(identity: "Identity", source: str,
        completed: tuple[str, ...]) -> str:
    return _digest({"contract":"protected-installer-0067-0073-v1",
        "sourceDigest":source,"database":identity.database,
        "host":identity.host,"port":identity.port,
        "completed":completed,"remaining":STEP_NAMES[len(completed):]})


def source_digest(root: Path = ROOT) -> str:
    root = Path(root).resolve()
    if root == Path(r"D:\运营管理系统").resolve():
        raise InstallerBlocked("formal/main checkout is not an isolated source")
    migration_root = root / "backend/ai_assistant/migrations"
    if any((match := re.match(r"^(\d{4})_", path.name))
            and int(match.group(1)) >= 74
            for path in migration_root.glob("0*.py")):
        raise InstallerBlocked("migration after 0073 is not in the allowlist")
    actual = {}
    for relative, expected in PINNED_SOURCE_SHA256.items():
        source = root / relative
        if not source.is_file() or source.is_symlink():
            raise InstallerBlocked("reviewed migration source is missing or redirected")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != expected:
            raise InstallerBlocked("reviewed migration source digest changed: " +
                relative)
        actual[relative] = digest
    return _digest(actual)


@dataclass(frozen=True)
class Identity:
    source_root: Path
    run_root: Path
    environment: str
    host: str
    port: int
    database: str
    admin_role: str
    ordinary_role: str

    def validate(self) -> None:
        source = Path(self.source_root).resolve()
        run = Path(self.run_root).resolve()
        expected_parent = (source / ".runtime").resolve()
        if (source == Path(r"D:\运营管理系统").resolve()
                or run.parent != expected_parent
                or re.fullmatch(r"ai-pg-[0-9a-f]{12}", run.name) is None
                or not run.is_dir() or run.is_symlink()
                or getattr(run, "is_junction", lambda: False)()
                or self.environment != "test"
                or self.host != "127.0.0.1"
                or type(self.port) is not int
                or not 55440 <= self.port <= 55999
                or self.database != CLONE_DATABASE
                or self.admin_role != ADMIN_ROLE
                or self.ordinary_role != ORDINARY_ROLE):
            raise InstallerBlocked("protected installer is isolated-test only")


@dataclass(frozen=True)
class RoleState:
    can_login: bool
    inherit: bool
    superuser: bool
    createdb: bool
    createrole: bool
    replication: bool
    bypassrls: bool
    password_is_null: bool
    membership_count: int

    def closed(self) -> bool:
        return (self.can_login is False and self.inherit is False
            and self.superuser is False and self.createdb is False
            and self.createrole is False and self.replication is False
            and self.bypassrls is False and self.password_is_null is True
            and type(self.membership_count) is int
            and self.membership_count == 0)


@dataclass(frozen=True)
class MigrationEntry:
    app: str
    name: str
    backwards: bool = False


@dataclass(frozen=True)
class Snapshot:
    applied: tuple[str, ...]
    verified_catalogs: frozenset[str]
    roles: Mapping[str, RoleState]
    django_plan: tuple[MigrationEntry, ...]


@dataclass(frozen=True)
class Plan:
    digest: str
    source_digest: str
    completed: tuple[str, ...]
    remaining: tuple[str, ...]
    next_step: Step | None


def _prefix(snapshot: Snapshot) -> tuple[str, ...]:
    if BASELINE not in snapshot.applied:
        raise InstallerBlocked("0066 predecessor receipt is absent")
    if any(name >= "0066_" and name not in (BASELINE, *STEP_NAMES)
            for name in snapshot.applied):
        raise InstallerBlocked("unapproved migration after 0066 exists")
    applied = tuple(name for name in STEP_NAMES if name in snapshot.applied)
    if applied != STEP_NAMES[:len(applied)]:
        raise InstallerBlocked("protected migration receipts are not a prefix")
    if snapshot.verified_catalogs != frozenset(applied):
        raise InstallerBlocked("protected catalog witness is not the exact prefix")
    for name, state in snapshot.roles.items():
        if name not in ROLE_NAMES or not state.closed():
            raise InstallerBlocked("protected role properties or membership drift")
    expected_roles = {role for step in STEPS[:len(applied)]
        for role in step.roles}
    if not expected_roles <= set(snapshot.roles):
        raise InstallerBlocked("completed protected migration lacks role")
    return applied


def build_plan(identity: Identity, snapshot: Snapshot,
        *, root: Path = ROOT) -> Plan:
    identity.validate()
    if Path(root).resolve() != Path(identity.source_root).resolve():
        raise InstallerBlocked("installer source root differs from isolated identity")
    digest = source_digest(root)
    completed = _prefix(snapshot)
    remaining = STEP_NAMES[len(completed):]
    expected = tuple(MigrationEntry("ai_assistant", name) for name in remaining)
    if snapshot.django_plan != expected:
        raise InstallerBlocked("Django migration plan has an extra, reverse, or missing step")
    next_step = STEPS[len(completed)] if remaining else None
    plan_digest = _plan_digest(identity, digest, completed)
    return Plan(plan_digest, digest, completed, remaining, next_step)


class Adapter(Protocol):
    def snapshot(self) -> Snapshot: ...
    def preprovision(self, roles: tuple[str, ...]) -> None: ...
    def migrate_one(self, step: Step) -> None: ...


class JsonLedger:
    """Create-only per-step intents; no credentials or raw database rows."""

    def __init__(self, identity: Identity):
        identity.validate()
        self.identity = identity
        self.root = Path(identity.run_root) / "protected-installer-ledger"

    def _file(self, operation_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", operation_id):
            raise InstallerBlocked("installer operation id is invalid")
        return self.root / (operation_id + ".json")

    def _lock(self, step_name: str) -> Path:
        if step_name not in STEP_NAMES:
            raise InstallerBlocked("installer step lock name is invalid")
        return self.root / (step_name + ".lock")

    def start(self, plan: Plan) -> str:
        if plan.next_step is None:
            raise InstallerBlocked("no protected migration remains")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if (self.root.is_symlink() or
                getattr(self.root, "is_junction", lambda: False)()):
            raise InstallerBlocked("installer ledger directory is redirected")
        operation_id = secrets.token_hex(16)
        lock = self._lock(plan.next_step.name)
        try:
            # Deterministic per-step O_EXCL is the single-consumer boundary.
            # A crash after lock creation stays blocked until an audited repair.
            with lock.open("x", encoding="utf-8") as stream:
                json.dump({"operationId":operation_id,
                    "step":plan.next_step.name,"planDigest":plan.digest},
                    stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            raise InstallerBlocked("protected migration step lock exists") from None
        entry = {"schemaVersion":"protected-installer-operation-v1",
            "operationId":operation_id,"step":plan.next_step.name,
            "planDigest":plan.digest,"sourceDigest":plan.source_digest,
            "completedBefore":list(plan.completed),"status":"prepared",
            "recordedAt":datetime.now(timezone.utc).isoformat(),
            "diagnosticSha256":None}
        with self._file(operation_id).open("x", encoding="utf-8") as stream:
            json.dump(entry, stream, ensure_ascii=True, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        return operation_id

    def read(self, operation_id: str) -> dict[str, object]:
        path = self._file(operation_id)
        if not path.is_file() or path.is_symlink():
            raise InstallerBlocked("installer operation receipt is missing")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise InstallerBlocked("installer operation receipt is invalid") from None
        expected_fields = {"schemaVersion", "operationId", "step",
            "planDigest", "sourceDigest", "completedBefore", "status",
            "recordedAt", "diagnosticSha256"}
        if (not isinstance(value, dict) or set(value) != expected_fields
                or value["operationId"] != operation_id
                or value["schemaVersion"] != "protected-installer-operation-v1"
                or value["step"] not in STEP_NAMES
                or type(value["completedBefore"]) is not list
                or value["completedBefore"] != list(STEP_NAMES[:STEP_NAMES.index(
                    value["step"])])
                or value["status"] not in ("prepared", "unknown", "committed")
                or type(value["sourceDigest"]) is not str
                or HEX64.fullmatch(value["sourceDigest"]) is None
                or type(value["planDigest"]) is not str
                or value["planDigest"] != _plan_digest(self.identity,
                    value["sourceDigest"],tuple(value["completedBefore"]))
                or value["sourceDigest"] != source_digest(
                    self.identity.source_root)
                or (value["diagnosticSha256"] is not None and
                    (type(value["diagnosticSha256"]) is not str or
                     HEX64.fullmatch(value["diagnosticSha256"]) is None))
                or type(value["recordedAt"]) is not str):
            raise InstallerBlocked("installer operation receipt is invalid")
        try:
            recorded_at = datetime.fromisoformat(value["recordedAt"])
        except ValueError:
            raise InstallerBlocked("installer operation time is invalid") from None
        if recorded_at.tzinfo is None:
            raise InstallerBlocked("installer operation time lacks timezone")
        lock = self._lock(value["step"])
        if not lock.is_file() or lock.is_symlink():
            raise InstallerBlocked("installer step lock is missing or redirected")
        try:
            locked = json.loads(lock.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise InstallerBlocked("installer step lock is invalid") from None
        if locked != {"operationId":operation_id,"step":value["step"],
                "planDigest":value["planDigest"]}:
            raise InstallerBlocked("installer step lock differs from receipt")
        return value

    def transition(self, operation_id: str, status: str,
            diagnostic_sha256: str | None = None) -> dict[str, object]:
        if status not in ("unknown", "committed"):
            raise InstallerBlocked("installer outcome transition is invalid")
        value = self.read(operation_id)
        if value.get("status") not in ("prepared", "unknown"):
            raise InstallerBlocked("installer operation already terminal")
        if diagnostic_sha256 is not None and HEX64.fullmatch(
                diagnostic_sha256) is None:
            raise InstallerBlocked("installer diagnostic digest is invalid")
        value["status"] = status
        value["diagnosticSha256"] = diagnostic_sha256
        value["recordedAt"] = datetime.now(timezone.utc).isoformat()
        path = self._file(operation_id)
        temporary = path.with_name(path.name + ".new-" + secrets.token_hex(8))
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=True, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return value


def apply_one(identity: Identity, adapter: Adapter, ledger: JsonLedger,
        *, approved_plan_digest: str, root: Path = ROOT) -> dict[str, object]:
    """Attempt exactly one step, then stop; any uncertain result stays unknown."""
    identity.validate()
    before = build_plan(identity, adapter.snapshot(), root=root)
    if before.next_step is None or approved_plan_digest != before.digest:
        raise InstallerBlocked("approved isolated plan digest is absent or stale")
    operation_id = ledger.start(before)
    try:
        adapter.preprovision(before.next_step.roles)
        middle = build_plan(identity, adapter.snapshot(), root=root)
        if middle.completed != before.completed or middle.next_step != before.next_step:
            raise InstallerBlocked("role preprovision changed migration identity")
        adapter.migrate_one(before.next_step)
        after = build_plan(identity, adapter.snapshot(), root=root)
        if after.completed != (*before.completed, before.next_step.name):
            raise InstallerBlocked("migration result is not an exact verified prefix")
    except BaseException as error:
        ledger.transition(operation_id, "unknown",
            hashlib.sha256(type(error).__name__.encode("ascii")).hexdigest())
        return {"status":"unknown", "operationId":operation_id,
            "step":before.next_step.name, "replayAllowed":False}
    ledger.transition(operation_id, "committed")
    return {"status":"committed", "operationId":operation_id,
        "step":before.next_step.name, "replayAllowed":False}


def audit_unknown(identity: Identity, adapter: Adapter, ledger: JsonLedger,
        operation_id: str, *, root: Path = ROOT) -> dict[str, object]:
    """Read-only reconciliation; absence never grants permission to retry."""
    identity.validate()
    entry = ledger.read(operation_id)
    if entry.get("status") not in ("prepared", "unknown"):
        raise InstallerBlocked("operation does not require reconciliation")
    snapshot = adapter.snapshot()
    plan = build_plan(identity, snapshot, root=root)
    before = tuple(entry.get("completedBefore", ()))
    selected = entry.get("step")
    if (entry.get("sourceDigest") != plan.source_digest
            or entry.get("planDigest") != _plan_digest(identity,
                plan.source_digest,before)):
        return {"status":"unknown", "operationId":operation_id,
            "replayAllowed":False}
    if plan.completed == (*before, selected):
        ledger.transition(operation_id, "committed")
        return {"status":"committed", "operationId":operation_id,
            "replayAllowed":False}
    return {"status":"unknown", "operationId":operation_id,
        "replayAllowed":False}

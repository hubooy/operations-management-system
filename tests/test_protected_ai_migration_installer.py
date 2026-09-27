"""Pure, synthetic default-closed protected migration installer checks."""

from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import protected_ai_migration_installer as installer


CLOSED = installer.RoleState(False, False, False, False, False,
    False, False, True, 0)


class FakeAdapter:
    def __init__(self, *, completed=0, fail_after_apply=False,
            fail_after_roles=False):
        self.completed = completed
        self.roles = {role: CLOSED for step in installer.STEPS[:completed]
            for role in step.roles}
        self.fail_after_apply = fail_after_apply
        self.fail_after_roles = fail_after_roles
        self.calls = []

    def snapshot(self):
        names = installer.STEP_NAMES[:self.completed]
        return installer.Snapshot((installer.BASELINE, *names),
            frozenset(names), dict(self.roles),
            tuple(installer.MigrationEntry("ai_assistant", name)
                for name in installer.STEP_NAMES[self.completed:]))

    def preprovision(self, roles):
        self.calls.append(("roles", roles))
        self.roles.update({role:CLOSED for role in roles})
        if self.fail_after_roles:
            raise ConnectionError("synthetic role reply lost")

    def migrate_one(self, step):
        self.calls.append(("migration", step.name, step.installer))
        self.completed += 1
        if self.fail_after_apply:
            raise ConnectionError("synthetic migration reply lost")


class ProtectedMigrationInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="protected-installer-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "project"
        for relative in installer.PINNED_SOURCE_SHA256:
            source = installer.ROOT / relative
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        self.run_root = self.root / ".runtime" / "ai-pg-0123456789ab"
        self.run_root.mkdir(parents=True)
        self.identity = installer.Identity(self.root, self.run_root, "test",
            "127.0.0.1", 55886, installer.CLONE_DATABASE,
            installer.ADMIN_ROLE, installer.ORDINARY_ROLE)

    def _plan(self, adapter):
        return installer.build_plan(self.identity, adapter.snapshot(),
            root=self.root)

    def _ledger(self):
        return installer.JsonLedger(self.identity)

    def test_pinned_source_and_0074_are_default_closed(self):
        self.assertRegex(installer.source_digest(self.root), r"^[0-9a-f]{64}$")
        self.assertEqual(len(installer.STEPS), 7)
        self.assertEqual(len(installer.ROLE_NAMES), 13)
        self.assertTrue(all(step.installer in ("ordinary", "privileged")
            for step in installer.STEPS))
        source = self.root / next(iter(installer.PINNED_SOURCE_SHA256))
        source.write_bytes(source.read_bytes() + b"\nchanged")
        with self.assertRaisesRegex(installer.InstallerBlocked, "digest changed"):
            installer.source_digest(self.root)
        shutil.copyfile(installer.ROOT /
            next(iter(installer.PINNED_SOURCE_SHA256)), source)
        future = self.root / "backend/ai_assistant/migrations/0074_fake.py"
        future.write_text("# not reviewed\n", encoding="utf-8")
        with self.assertRaisesRegex(installer.InstallerBlocked,
                "after 0073 is not in"):
            installer.source_digest(self.root)
        future.unlink()
        (future.parent / "0075_fake.py").write_text("# not reviewed\n",
            encoding="utf-8")
        with self.assertRaisesRegex(installer.InstallerBlocked,
                "after 0073 is not in"):
            installer.source_digest(self.root)

    def test_isolation_identity_rejects_formal_and_wrong_roots(self):
        self.identity.validate()
        for changed in (
                replace(self.identity, environment="production"),
                replace(self.identity, host="localhost"),
                replace(self.identity, port=5432),
                replace(self.identity, database="teruisi_sales"),
                replace(self.identity, admin_role="postgres"),
                replace(self.identity, run_root=self.root / "outside"),
                replace(self.identity, source_root=Path(r"D:\运营管理系统"))):
            with self.subTest(changed=changed), self.assertRaises(
                    installer.InstallerBlocked):
                changed.validate()

    def test_exact_forward_plan_and_catalog_prefix_only(self):
        adapter = FakeAdapter()
        plan = self._plan(adapter)
        self.assertEqual(plan.remaining, installer.STEP_NAMES)
        self.assertEqual(plan.next_step, installer.STEPS[0])
        extra = adapter.snapshot()
        for wrong in (
                replace(extra, django_plan=(installer.MigrationEntry(
                    "finance", "0005_raw_column_evidence_v2"),
                    *extra.django_plan)),
                replace(extra, django_plan=(installer.MigrationEntry(
                    "ai_assistant", installer.STEP_NAMES[0], True),
                    *extra.django_plan[1:])),
                replace(extra, applied=(installer.BASELINE,
                    installer.STEP_NAMES[1])),
                replace(extra, applied=(installer.BASELINE,
                    "0067_unapproved_sidecar")),
                replace(extra, applied=(installer.BASELINE,"0074_fake")),
                replace(extra, verified_catalogs=frozenset({
                    installer.STEP_NAMES[0]}))):
            with self.subTest(wrong=wrong), self.assertRaises(
                    installer.InstallerBlocked):
                installer.build_plan(self.identity, wrong, root=self.root)
        adapter = FakeAdapter(completed=2)
        adapter.roles[installer.STEPS[0].roles[0]] = replace(CLOSED,
            can_login=True)
        with self.assertRaisesRegex(installer.InstallerBlocked,
                "role properties"):
            self._plan(adapter)

    def test_one_step_success_does_not_chain_another(self):
        adapter = FakeAdapter()
        plan = self._plan(adapter)
        ledger = self._ledger()
        result = installer.apply_one(self.identity, adapter, ledger,
            approved_plan_digest=plan.digest, root=self.root)
        self.assertEqual((result["status"], result["step"],
            result["replayAllowed"]),
            ("committed", installer.STEP_NAMES[0], False))
        self.assertEqual(adapter.completed, 1)
        self.assertEqual(adapter.calls, [("roles", installer.STEPS[0].roles),
            ("migration", installer.STEP_NAMES[0], "ordinary")])
        self.assertEqual(ledger.read(result["operationId"])["status"],
            "committed")
        self.assertEqual(self._plan(adapter).next_step, installer.STEPS[1])

    def test_lost_migration_reply_never_replays_and_explicit_audit_can_confirm(self):
        adapter = FakeAdapter(fail_after_apply=True)
        plan = self._plan(adapter)
        ledger = self._ledger()
        result = installer.apply_one(self.identity, adapter, ledger,
            approved_plan_digest=plan.digest, root=self.root)
        self.assertEqual((result["status"],adapter.completed), ("unknown",1))
        self.assertFalse(result["replayAllowed"])
        self.assertEqual(ledger.read(result["operationId"])["status"],
            "unknown")
        with self.assertRaises(installer.InstallerBlocked):
            installer.apply_one(self.identity, adapter, ledger,
                approved_plan_digest=plan.digest, root=self.root)
        audited = installer.audit_unknown(self.identity, adapter, ledger,
            result["operationId"], root=self.root)
        self.assertEqual((audited["status"],audited["replayAllowed"]),
            ("committed",False))
        self.assertEqual(ledger.read(result["operationId"])["status"],
            "committed")

    def test_lost_role_reply_with_absent_receipt_stays_unknown(self):
        adapter = FakeAdapter(fail_after_roles=True)
        plan = self._plan(adapter)
        ledger = self._ledger()
        result = installer.apply_one(self.identity, adapter, ledger,
            approved_plan_digest=plan.digest, root=self.root)
        self.assertEqual((result["status"],adapter.completed), ("unknown",0))
        self.assertEqual(installer.audit_unknown(self.identity, adapter,
            ledger, result["operationId"], root=self.root)["status"],
            "unknown")
        with self.assertRaisesRegex(installer.InstallerBlocked,
                "step lock exists"):
            installer.apply_one(self.identity, adapter, ledger,
                approved_plan_digest=plan.digest, root=self.root)

    def test_atomic_step_lock_rejects_concurrent_callers(self):
        plan = self._plan(FakeAdapter())
        barrier = threading.Barrier(2)

        def attempt(_):
            ledger = self._ledger()
            barrier.wait(timeout=5)
            try:
                return ("won",ledger.start(plan))
            except installer.InstallerBlocked:
                return ("blocked",None)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sorted(item[0] for item in results),
            ["blocked","won"])
        paths = list((self.run_root / "protected-installer-ledger").iterdir())
        self.assertEqual(sorted(path.suffix for path in paths),
            [".json",".lock"])
        winner = next(value for status,value in results if status == "won")
        self.assertEqual(self._ledger().read(winner)["status"],"prepared")

    def test_receipt_and_lock_tamper_cannot_confirm_unknown(self):
        plan = self._plan(FakeAdapter())
        ledger = self._ledger()
        operation = ledger.start(plan)
        receipt = ledger._file(operation)
        original = json.loads(receipt.read_text(encoding="utf-8"))
        mutations = (
            {**original,"step":installer.STEP_NAMES[1]},
            {**original,"completedBefore":[installer.STEP_NAMES[0]]},
            {**original,"planDigest":"0"*64},
            {**original,"sourceDigest":"0"*64},
            {**original,"extra":True},
            {**original,"status":"replayed"},
        )
        for value in mutations:
            receipt.write_text(json.dumps(value),encoding="utf-8")
            with self.assertRaises(installer.InstallerBlocked):
                ledger.read(operation)
        receipt.write_text(json.dumps(original),encoding="utf-8")
        lock = ledger._lock(installer.STEP_NAMES[0])
        locked = lock.read_text(encoding="utf-8")
        lock.write_text('{"operationId":"forged"}',encoding="utf-8")
        with self.assertRaises(installer.InstallerBlocked):
            ledger.read(operation)
        lock.write_text(locked,encoding="utf-8")
        self.assertEqual(ledger.read(operation)["status"],"prepared")


if __name__ == "__main__":
    unittest.main()

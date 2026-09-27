"""No database: the isolated adapter has no implicit production entry."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import protected_ai_migration_isolated as isolated
import protected_ai_migration_installer as contract


class IsolatedInstallerStaticTests(unittest.TestCase):
    def test_url_parser_never_accepts_formal_port_or_identity(self):
        synthetic = ("postgresql://teruisi_sales_owner:synthetic-password@"
            "127.0.0.1:55886/teruisi_ai_migration_role_probe")
        self.assertEqual(isolated._url_identity(synthetic),
            ("127.0.0.1",55886,contract.CLONE_DATABASE,
             contract.ORDINARY_ROLE))
        for changed in (synthetic.replace("55886", "5432"),
                synthetic.replace(contract.CLONE_DATABASE,"teruisi_sales"),
                synthetic.replace("127.0.0.1","localhost"),
                synthetic.replace("synthetic-password", ""),
                synthetic + "?hostaddr=10.0.0.8",
                synthetic + "#other"):
            with self.subTest(changed=changed), self.assertRaises(
                    contract.InstallerBlocked):
                isolated._url_identity(changed)

    def test_cli_without_synthetic_urls_fails_closed_without_files(self):
        with tempfile.TemporaryDirectory(prefix="installer-cli-") as temporary:
            run = Path(temporary) / "ai-pg-0123456789ab"
            run.mkdir()
            env = {key:value for key,value in os.environ.items()
                if key not in ("TERUISI_PROTECTED_INSTALLER_ADMIN_URL",
                    "TERUISI_PROTECTED_INSTALLER_ORDINARY_URL")}
            result = subprocess.run([sys.executable,
                ROOT / "tools/protected_ai_migration_isolated.py", "--plan",
                "--run-root", run], cwd=ROOT, env=env,
                capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW
                    if os.name == "nt" else 0)
            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "blocked")
            self.assertFalse(payload["formalAllowed"])
            self.assertEqual(payload["reasonCode"], "url_identity_invalid")
            self.assertEqual(list(run.iterdir()), [])
            self.assertNotIn(b"password", result.stdout.lower())

    def test_reason_code_never_reflects_sensitive_exception_text(self):
        secret = "postgresql://role:synthetic-secret@127.0.0.1:55786/db"
        self.assertEqual(isolated._safe_reason(RuntimeError(secret)),
            "unexpected_error")
        self.assertEqual(isolated._safe_reason(contract.InstallerBlocked(secret)),
            "blocked_unclassified")
        self.assertNotIn("synthetic-secret", isolated._safe_reason(
            contract.InstallerBlocked(secret)))

    def test_django_setup_from_root_and_tools_cwd(self):
        source = ("import sys; sys.path.insert(0, sys.argv[1]); "
            "import protected_ai_migration_isolated as isolated; "
            "isolated._ensure_backend_import_path(); import django; "
            "django.setup(); print('django_setup_ok')")
        env = {**os.environ,
            "DJANGO_SETTINGS_MODULE":"teruisi_backend.settings",
            "TERUISI_DJANGO_ENVIRONMENT":"test",
            "TERUISI_DJANGO_PROCESS_ROLE":"development",
            "DJANGO_SECRET_KEY":"synthetic-only",
            "TERUISI_DJANGO_INTERNAL_SECRET":"synthetic-only"}
        for cwd in (ROOT, ROOT / "tools"):
            with self.subTest(cwd=cwd):
                result = subprocess.run([sys.executable, "-c", source,
                    str(ROOT / "tools")], cwd=cwd, env=env,
                    capture_output=True, timeout=15,
                    creationflags=subprocess.CREATE_NO_WINDOW
                        if os.name == "nt" else 0)
                self.assertEqual(result.returncode, 0,
                    "isolated Django bootstrap failed")
                self.assertEqual(result.stdout.strip(), b"django_setup_ok")

    def test_no_formal_service_or_backup_adapter_is_imported(self):
        source = (ROOT / "tools/protected_ai_migration_isolated.py"
            ).read_text(encoding="utf-8")
        self.assertIn("ordinary installer can read private verifier key", source)
        for forbidden in ("django-local-service.ps1",
                "django-postgres-maintenance.ps1",
                "postgres-consistent-backup.py", "Stop-Service",
                "DeployApp", "PrepareApp", "KEY_OWNER TO teruisi_sales_owner",
                "GRANT SELECT ON public.protected_business_budget_v11_verifier_keys"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()

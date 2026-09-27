"""CLI entry checks only; owning PostgreSQL behavior is tested separately."""
import io
import json
from unittest.mock import patch, sentinel

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError
from django.test import SimpleTestCase, override_settings

from sales.management.commands import rebuild_analysis_options as command


@override_settings(DJANGO_PROCESS_ROLE="sales_writer", DJANGO_EXPECT_READ_ONLY=False)
class SalesOptionsCommandTests(SimpleTestCase):
    def setUp(self):
        self.actor = {"email": "admin@example.invalid", "role": "admin", "scope": None, "status": "active", "version": 3}
        self.output = io.StringIO()
        self.manager = self.enterContext(patch.object(command.AppUser, "objects"))
        self.manager.filter.return_value.values.return_value.first.return_value = self.actor
        self.prepare = self.enterContext(patch.object(command.projection, "prepare_rebuild", return_value=sentinel.prepared))
        self.publish = self.enterContext(patch.object(command.projection, "publish_rebuild", return_value={
            "generation": "a" * 32, "identityCount": 7, "directoryDigest": "b" * 64, "coverageVerified": False,
        }))

    def run_command(self, **kwargs):
        call_command("rebuild_analysis_options", actor_email="admin@example.invalid", stdout=self.output, **kwargs)

    def test_explicit_actor_required_and_invalid_actor_rejected_before_database(self):
        with self.assertRaises(CommandError):
            call_command("rebuild_analysis_options", stdout=self.output)
        for value in ("", " ", "invalid", "x" * 321, None):
            with self.subTest(value=value), self.assertRaises(CommandError):
                command.Command(stdout=self.output).handle(actor_email=value)
        self.manager.filter.assert_not_called()
        self.prepare.assert_not_called()

    def test_non_writer_and_read_only_rejected_before_database(self):
        for role, readonly in (("development", False), ("reader", True), ("migration_writer", False), ("sales_writer", True)):
            with self.subTest(role=role), override_settings(DJANGO_PROCESS_ROLE=role, DJANGO_EXPECT_READ_ONLY=readonly):
                with self.assertRaises(CommandError): self.run_command()
        self.manager.filter.assert_not_called()
        self.prepare.assert_not_called()

    def test_atomic_caller_rejected_before_database(self):
        with patch.object(command.connection, "in_atomic_block", True), self.assertRaises(CommandError):
            self.run_command()
        self.manager.filter.assert_not_called()
        self.prepare.assert_not_called()

    def test_missing_disabled_scoped_and_non_admin_are_not_synthesized(self):
        for actor in (None, {**self.actor, "status": "disabled"}, {**self.actor, "role": "operator"},
                      {**self.actor, "scope": {}}, {**self.actor, "scope": {"platforms": []}}):
            with self.subTest(actor=actor):
                self.manager.filter.return_value.values.return_value.first.return_value = actor
                with self.assertRaises(CommandError): self.run_command()
        self.prepare.assert_not_called()
        self.publish.assert_not_called()

    def test_prepare_then_publish_same_trusted_principal_and_minimal_output(self):
        sequence = []
        def prepare(principal):
            self.assertFalse(command.connection.in_atomic_block)
            sequence.append(("prepare", principal))
            return sentinel.prepared
        def publish(prepared, principal):
            self.assertIs(prepared, sentinel.prepared)
            self.assertIs(principal, sequence[0][1])
            sequence.append(("publish", principal))
            return {"generation": "a" * 32, "identityCount": 7, "coverageVerified": False}
        self.prepare.side_effect, self.publish.side_effect = prepare, publish
        command.Command(stdout=self.output).handle(actor_email=" ADMIN@EXAMPLE.INVALID ")
        self.manager.filter.assert_called_once_with(email="admin@example.invalid")
        self.manager.filter.return_value.values.assert_called_once_with("email", "role", "scope", "status", "version")
        self.assertEqual([entry[0] for entry in sequence], ["prepare", "publish"])
        self.assertEqual(sequence[0][1], command.Principal(self.actor["email"], "", "admin", None))
        self.assertEqual(json.loads(self.output.getvalue()), {"generation": "a" * 32, "identityCount": 7, "coverageVerified": False})

    def test_prepare_guard_failure_does_not_publish_or_retry(self):
        self.prepare.side_effect = command.projection.OptionsError("来源权限已变化", code="options_revision_changed", status=409)
        with self.assertRaisesMessage(CommandError, "来源权限已变化"): self.run_command()
        self.prepare.assert_called_once()
        self.publish.assert_not_called()
        self.assertEqual(self.output.getvalue(), "")

    def test_publish_guard_failure_does_not_retry_or_print_success(self):
        self.publish.side_effect = command.projection.OptionsError("销售权威已变化", code="conflict", status=409)
        with self.assertRaisesMessage(CommandError, "销售权威已变化"): self.run_command()
        self.prepare.assert_called_once()
        self.publish.assert_called_once()
        self.assertEqual(self.output.getvalue(), "")

    def test_database_error_does_not_expose_raw_database_detail(self):
        self.publish.side_effect = DatabaseError("raw SQL business data must not be printed")
        with self.assertRaises(CommandError) as raised: self.run_command()
        self.assertNotIn("raw SQL", str(raised.exception))
        self.assertEqual(self.output.getvalue(), "")
        self.publish.assert_called_once()

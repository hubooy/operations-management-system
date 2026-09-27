import io
from contextlib import ExitStack
from unittest.mock import Mock, patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase
from .management.commands import dingtalk_schedule as scheduler


class ScheduleWorkerTests(SimpleTestCase):
    def setUp(self):
        self.command = scheduler.Command(stdout=io.StringIO(), stderr=io.StringIO())
        self.config = {"enabled": True}
        self.lease = Mock()
        self.lease.__enter__ = Mock(return_value=self.lease)
        self.lease.__exit__ = Mock(return_value=False)
        self.lease.execute.return_value.fetchone.return_value = (True,)
        self.database = Mock(vendor="postgresql")
        self.database.get_connection_params.return_value = {}
        self.database.Database.connect.return_value = self.lease

    def handle(self):
        self.command.handle(config="fixture", bot_credentials="fixture", screenshot_profile="")

    def adapters(self, stack):
        patches = (
            (scheduler, "connection", self.database),
            (scheduler, "authority", Mock()),
            (scheduler.dingtalk, "load_config", Mock(return_value=self.config)),
            (scheduler.dingtalk_settings, "effective", Mock(return_value=self.config)),
            (scheduler.platform, "credentials", Mock(return_value=("fixture", "fixture"))),
            (scheduler.platform, "open_stream", Mock(side_effect=AssertionError("Stream unavailable"))),
            (scheduler.dingtalk_schedules, "recover_interrupted", Mock()),
        )
        for obj, name, value in patches: stack.enter_context(patch.object(obj, name, value))

    def test_schedule_dispatch_runs_without_stream_and_uses_separate_process_lock(self):
        def step(reader, send, media):
            self.assertEqual(reader(), self.config)
            send("fixture-session", "fixture-result")
            return True
        with ExitStack() as stack:
            self.adapters(stack)
            stack.enter_context(patch.object(scheduler.dingtalk_schedules, "step", side_effect=step))
            send = stack.enter_context(patch.object(scheduler.platform, "send"))
            stack.enter_context(patch.object(scheduler.time, "sleep", side_effect=KeyboardInterrupt))
            self.handle()
            send.assert_called_once()
            scheduler.platform.open_stream.assert_not_called()
            scheduler.dingtalk_schedules.recover_interrupted.assert_called_once()
        self.lease.execute.assert_any_call("SELECT pg_try_advisory_lock(841327,1909)")
        self.lease.__exit__.assert_called_once()

    def test_old_combined_receiver_or_duplicate_scheduler_blocks_before_recovery_or_send(self):
        self.lease.execute.return_value.fetchone.return_value = (False,)
        with ExitStack() as stack:
            self.adapters(stack)
            run = stack.enter_context(patch.object(self.command, "run_schedules"))
            with self.assertRaises(CommandError): self.handle()
            run.assert_not_called()
            scheduler.dingtalk_schedules.recover_interrupted.assert_not_called()
            scheduler.platform.credentials.assert_not_called()

    def test_lost_lock_connection_fails_before_dispatch_instead_of_reconnecting(self):
        self.lease.execute.side_effect = [Mock(fetchone=Mock(return_value=(True,))), RuntimeError("private-db-error")]
        with ExitStack() as stack:
            self.adapters(stack)
            run = stack.enter_context(patch.object(self.command, "run_schedules"))
            with self.assertRaises(CommandError) as error: self.handle()
            self.assertNotIn("private", str(error.exception))
            run.assert_not_called()
        self.database.Database.connect.assert_called_once()

    def test_missing_credentials_never_consumes_queue_or_recovers_unknown_runs(self):
        with ExitStack() as stack:
            self.adapters(stack)
            scheduler.platform.credentials.side_effect = RuntimeError("private-secret")
            run = stack.enter_context(patch.object(self.command, "run_schedules"))
            with self.assertRaises(CommandError) as error: self.handle()
            self.assertNotIn("private", str(error.exception))
            run.assert_not_called()
            scheduler.dingtalk_schedules.recover_interrupted.assert_not_called()

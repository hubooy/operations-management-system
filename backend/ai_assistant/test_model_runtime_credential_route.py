"""Signed model-runtime read uses writer credentials without a write receipt."""
import json
from unittest.mock import patch

from django import test as djtest
from django.test import RequestFactory

from . import views
from .policy import Principal


class ModelRuntimeCredentialRouteTests(djtest.TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.actor = Principal("model-reader@example.invalid", "Model reader",
            "admin", None)

    def _call(self, process_role, operation):
        request = self.factory.post("/api/ai/consumer",
            data=json.dumps({"operation": operation, "id": "model-1"}),
            content_type="application/json",
            HTTP_X_TERUISI_REQUEST_ID="model-runtime-route-test")
        with djtest.override_settings(DJANGO_PROCESS_ROLE=process_role), \
                patch.object(views, "verify_principal", return_value=self.actor), \
                patch.object(views, "current_principal", return_value=self.actor), \
                patch.object(views, "authority"), \
                patch.object(views, "consumer", return_value={
                    "model": {"id": "model-1",
                        "api_key_encrypted": "test-only-ciphertext"}}) as consumer, \
                patch.object(views, "write") as write:
            result = views._dispatch(request, "consumer")
        return result, consumer, write

    def test_runtime_writer_read_returns_same_payload_without_receipt(self):
        result, consumer, write = self._call("ai_writer", "model-runtime")
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(json.loads(result.content)["model"]["id"], "model-1")
        self.assertEqual(json.loads(result.content)["model"][
            "api_key_encrypted"], "test-only-ciphertext")
        consumer.assert_called_once()
        write.assert_not_called()
        blocked, consumer, write = self._call("ai_reader", "model-runtime")
        self.assertEqual(blocked.status_code, 403)
        consumer.assert_not_called()
        write.assert_not_called()

    def test_model_list_preserves_response_on_writer_without_mutation(self):
        result, consumer, write = self._call("ai_writer", "model-list")
        self.assertEqual(result.status_code, 200)
        consumer.assert_called_once()
        write.assert_not_called()
        blocked, consumer, write = self._call("ai_reader", "model-list")
        self.assertEqual(blocked.status_code, 403)
        consumer.assert_not_called()
        write.assert_not_called()

    def test_admin_model_settings_get_moves_to_writer_without_mutation(self):
        request = self.factory.get("/api/ai/models",
            HTTP_X_TERUISI_REQUEST_ID="model-settings-read-test")
        for process_role, status in (("ai_reader", 403), ("ai_writer", 200)):
            with self.subTest(process_role=process_role), djtest.override_settings(
                    DJANGO_PROCESS_ROLE=process_role), patch.object(views,
                    "verify_principal", return_value=self.actor), patch.object(
                    views, "current_principal", return_value=self.actor), patch.object(
                    views, "authority"), patch.object(views, "read",
                    return_value={"items": [{"id": "model-1",
                        "apiKeySuffix": "test"}]}) as read, patch.object(
                    views, "write") as write:
                result = views._dispatch(request, "models")
            self.assertEqual(result.status_code, status)
            if status == 200:
                self.assertEqual(json.loads(result.content)["items"][0][
                    "apiKeySuffix"], "test")
                read.assert_called_once()
            else:
                read.assert_not_called()
            write.assert_not_called()

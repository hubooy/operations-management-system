"""Reader-facing model lists use only columns their response needs."""
from django import test as djtest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from . import chat, configuration, database_contract, views
from . import test_business_market_v2_model_transport_owner as fixture
from .test_business_market_v2_material_role_bridge import session_role


@djtest.override_settings(DJANGO_PROCESS_ROLE="ai_reader",
    DJANGO_ENVIRONMENT="test")
class ModelReaderProjectionTests(djtest.TransactionTestCase):
    def setUp(self):
        fixture.MarketV2ModelTransportOwnerTests.setUp(self)
        # The fingerprint test intentionally accepts any canonical JSON;
        # the existing administration response validates runtime option keys.
        self.model.generation_options_json = "{}"
        self.model.base_url = "https://api.openai.com/v1"
        self.model.save(update_fields=["generation_options_json", "base_url"])

    def test_reader_chat_list_and_writer_model_lists_preserve_fields(self):
        # Model settings and internal model-list remain on writer.  This test
        # uses the exact 19-column reader contract for chat and report checks.
        with connection.cursor() as cursor:
            cursor.execute("GRANT SELECT (" + ",".join(
                sorted(database_contract.MODEL_READER_COLUMNS)) +
                ") ON TABLE public.ai_models TO teruisi_ai_reader")
        with session_role("teruisi_ai_reader"), CaptureQueriesContext(
                connection) as captured:
            conversations = chat.listing({}, self.admin)
            model = configuration.resolve_model(self.model.id)
        self.assertEqual(set(configuration.MODEL_RUNTIME_READER_COLUMNS),
            database_contract.MODEL_READER_COLUMNS)
        self.assertEqual(conversations["models"][0]["id"], self.model.id)
        self.assertNotIn("apiKeySuffix", conversations["models"][0])
        self.assertEqual(model.id, self.model.id)
        self.assertEqual(model.generation_options_json, "{}")
        model_queries = [query["sql"] for query in captured.captured_queries
            if 'FROM "ai_models"' in query["sql"]]
        self.assertEqual(len(model_queries), 2)
        self.assertTrue(all("api_key_encrypted" not in statement
            for statement in model_queries))
        with session_role("teruisi_ai_writer"):
            available = views.consumer({"operation": "model-list",
                "modelType": "text"}, self.admin, "model-list-writer")
            admin = views.read(["models"], {}, self.admin)
        self.assertEqual(available["items"][0]["id"], self.model.id)
        self.assertEqual(available["items"][0]["modelName"],
            "fictional-v1")
        self.assertEqual(available["items"][0]["apiKeySuffix"], "read")
        self.assertEqual(admin["items"][0]["apiKeySuffix"], "read")

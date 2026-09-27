"""Isolated PostgreSQL proof for the exact AI model reader column ACL."""
import secrets

from django import test as djtest
from django.db import DatabaseError, connection

from . import health
from .database_contract import MODEL_READER_COLUMNS, provision
from .test_business_market_v2_material_role_bridge import session_role


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",
    DJANGO_ENVIRONMENT="test")
class ModelReaderColumnGrantTests(djtest.TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires isolated PostgreSQL roles")
        connection.ensure_connection()
        self.reader_password = secrets.token_hex(32)
        self.writer_password = secrets.token_hex(32)
        self.reprovision()

    def reprovision(self):
        provision(connection.connection, self.reader_password,
            self.writer_password)

    def grants(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT a.attname FROM pg_catalog.pg_attribute a "
                "WHERE a.attrelid='public.ai_models'::regclass "
                "AND a.attnum>0 AND NOT a.attisdropped "
                "AND pg_catalog.has_column_privilege("
                "'teruisi_ai_reader','public.ai_models',a.attname,'SELECT')")
            return {name for (name,) in cursor.fetchall()}

    def test_only_nineteen_reader_columns_and_no_table_or_secret_read(self):
        self.assertEqual(self.grants(), MODEL_READER_COLUMNS)
        with connection.cursor() as cursor:
            cursor.execute("SELECT has_table_privilege('teruisi_ai_reader',"
                "'public.ai_models','SELECT'),"
                "has_table_privilege('teruisi_ai_writer',"
                "'public.ai_models','SELECT')")
            self.assertEqual(cursor.fetchone(), (False, True))
        with session_role("teruisi_ai_reader"):
            with connection.cursor() as cursor:
                health._verify_model_reader_columns(cursor)
                cursor.execute("SELECT id,version,name,protocol,model_type,"
                    "model_name,base_url,is_default_text_model,status,"
                    "timeout_ms,reasoning_mode,temperature_milli,"
                    "max_tool_rounds,max_total_tool_calls,last_tested_at,"
                    "created_at,updated_at,generation_options_json,max_tokens "
                    "FROM public.ai_models LIMIT 0")
            for query in ("SELECT * FROM public.ai_models LIMIT 0",
                    "SELECT api_key_encrypted FROM public.ai_models LIMIT 0",
                    "SELECT api_key_suffix FROM public.ai_models LIMIT 0",
                    "SELECT last_test_result FROM public.ai_models LIMIT 0",
                    "UPDATE public.ai_models SET status='disabled' WHERE false"):
                with self.subTest(query=query), self.assertRaises(DatabaseError):
                    with connection.cursor() as cursor:
                        cursor.execute(query)

    def test_repeated_provision_removes_rogue_table_and_secret_column_grants(self):
        with connection.cursor() as cursor:
            cursor.execute("GRANT SELECT ON TABLE public.ai_models "
                "TO teruisi_ai_reader")
            cursor.execute("GRANT SELECT (api_key_encrypted) "
                "ON TABLE public.ai_models TO teruisi_ai_reader")
        with session_role("teruisi_ai_reader"), self.assertRaises(ValueError):
            with connection.cursor() as cursor:
                health._verify_model_reader_columns(cursor)
        self.reprovision()
        self.reprovision()
        self.assertEqual(self.grants(), MODEL_READER_COLUMNS)
        with session_role("teruisi_ai_reader"):
            with connection.cursor() as cursor:
                health._verify_model_reader_columns(cursor)
        with connection.cursor() as cursor:
            cursor.execute("GRANT SELECT (api_key_encrypted) "
                "ON TABLE public.ai_models TO teruisi_ai_reader")
        with session_role("teruisi_ai_reader"), self.assertRaises(ValueError):
            with connection.cursor() as cursor:
                health._verify_model_reader_columns(cursor)
        self.reprovision()
        self.assertEqual(self.grants(), MODEL_READER_COLUMNS)

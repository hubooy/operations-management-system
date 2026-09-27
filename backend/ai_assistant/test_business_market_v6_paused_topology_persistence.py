"""The isolated SQL client never repeats an unknown mutation."""
from unittest import TestCase
from unittest.mock import patch
from django.test import override_settings
from business_analysis.contracts import canonical, digest

from . import business_market_v6_paused_topology_persistence as writer
from .test_business_market_v6_paused_topology_contract import CLIENT, vector
from .business_market_v6_paused_topology_contract import build, cancel_request
from .policy import AiError


class IdentityDb:
    autocommit = True

    def __init__(self, address, role_ok=True, members=0):
        self.address = address
        self.role_ok = role_ok
        self.members = members

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, *_args):
        return None

    def fetchone(self):
        return (writer.ROLE, writer.ROLE, False,
            "test_teruisi_ai_rehearsal", self.address, 55880,
            self.role_ok, self.members)


class PausedTopologyPersistenceTests(TestCase):
    def built(self):
        proposal, cost, actor, quota = vector()
        return build(proposal, cost, actor, CLIENT, quota)

    def test_create_reply_unknown_never_repeats_or_calls_provider(self):
        built = self.built()
        with patch.object(writer, "_closed"), patch.object(writer, "_one",
                side_effect=RuntimeError("reply lost")) as database, patch(
                "ai_assistant.provider.turn") as provider:
            value = writer.create_once(object(), built, port=55880)
        self.assertEqual(value["status"], "unknown")
        self.assertEqual(value["phase"], "create_reply")
        self.assertFalse(value["retryAllowed"])
        self.assertEqual(database.call_count, 1)
        provider.assert_not_called()

    def test_oversize_graph_never_reaches_sql(self):
        built = self.built()
        huge = {"nodes": [{"instruction": "x" * 33_000}]}
        built["snapshot"]["graphDigest"] = digest(huge)
        built["snapshotJson"] = canonical(built["snapshot"])
        built["snapshotDigest"] = digest(built["snapshot"])
        built["intent"]["snapshotDigest"] = built["snapshotDigest"]
        built["intentJson"] = canonical(built["intent"])
        built["intentDigest"] = digest(built["intent"])
        with patch.object(writer,"_closed"), patch.object(writer.execution,
                "graph",return_value=huge), patch.object(writer,"_one") as sql:
            with self.assertRaises(AiError):
                writer.create_once(object(),built,port=55880)
        sql.assert_not_called()

    def test_pg_loopback_mask_identity_is_exact_and_other_ip_rejected(self):
        with override_settings(DJANGO_ENVIRONMENT="test",
                AI_MARKET_V6_PAUSED_TOPOLOGY_RECORD_ENABLED=True), \
                patch.dict("os.environ", {"TERUISI_DJANGO_ENVIRONMENT": "test"}):
            for address in ("127.0.0.1", "127.0.0.1/32", "::1", "::1/128"):
                with self.subTest(address=address):
                    writer._closed(IdentityDb(address), 55880)
            for address in ("127.0.0.2/32", "10.0.0.1/32", "::2/128", ""):
                with self.subTest(address=address), self.assertRaises(AiError):
                    writer._closed(IdentityDb(address), 55880)
            for kwargs in ({"role_ok": False}, {"members": 1}):
                with self.subTest(kwargs=kwargs), self.assertRaises(AiError):
                    writer._closed(IdentityDb("127.0.0.1/32", **kwargs),55880)

    def test_cancel_reply_unknown_never_repeats(self):
        built = self.built()
        request = cancel_request(built["snapshot"]["reportId"],
            built["snapshot"]["ownerEmail"], 1, "a" * 64)
        with patch.object(writer, "_closed"), patch.object(writer, "_one",
                side_effect=RuntimeError("reply lost")) as database:
            value = writer.cancel_once(object(), request, port=55880)
        self.assertEqual(value["status"], "unknown")
        self.assertFalse(value["retryAllowed"])
        self.assertEqual(database.call_count, 1)

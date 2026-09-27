"""Target isolated PG checks for the closed v4 followed-SKU owner."""
import json
from unittest.mock import patch

from django.test import TestCase
from access_control.models import AppUser

from . import business_v4_attributed_sku_relation as owner
from . import test_business_v4_netshop_promotion as fixture
from . import models as m
from .policy import AiError, canonical, digest


KEYS = {name:f"promotion-{name}" for name in
    ("current", "previous", "yearAgo")}
VIEW = "keyword_searchterm_plan_unit_match_attributed_sku"


class BusinessV4AttributedSkuRelationTests(TestCase):
    setUp = fixture.BusinessV4NetshopPromotionTests.setUp
    owner = fixture.BusinessV4NetshopPromotionTests.owner
    advance = fixture.BusinessV4NetshopPromotionTests.advance
    three_window_run = fixture.BusinessV4NetshopPromotionTests.three_window_run

    def complete(self):
        self.three_window_run()
        version = 1
        for window in ("previous", "yearAgo", "current"):
            first = self.advance(version, f"attr-{window}-first", KEYS[window])
            second = self.advance(first["runVersion"],
                f"attr-{window}-second", KEYS[window])
            version = second["runVersion"]

    def request(self, **changes):
        return {"sourceKeys":dict(KEYS), "window":"current",
            "view":VIEW, **changes}

    def test_one_selected_source_replays_and_binds_three_distinct_plan_sources(self):
        self.complete()
        before = (m.AiBusinessV4Chunk.objects.count(),
            m.AiBusinessV4ToolReceipt.objects.count())
        value = owner.page(self.parent.id, self.request(), self.principal,
            enabled=True)
        table, binding = value["table"], value["binding"]
        self.assertEqual(table["sourceRowCount"], 101)
        self.assertEqual(sum(row["currentRowCount"] for row in table["rows"]), 101)
        self.assertEqual(sum(row["metrics"]["spendCents"]["value"]
            for row in table["rows"]), 10_100)
        self.assertEqual(binding["runId"], self.parent.id)
        self.assertEqual(binding["sourceKeys"], KEYS)
        self.assertEqual(len({item["sourceRef"] for item in
            binding["sourceDirectory"].values()}), 3)
        self.assertEqual(binding["selectedWindow"], "current")
        self.assertFalse(binding["otherWindowsReplayed"])
        self.assertFalse(binding["sameSourceAmountsAdded"])
        self.assertFalse(binding["agentReadPersisted"])
        self.assertFalse(value["authorityVerified"])
        self.assertEqual(value["bindingDigest"], digest(binding))
        self.assertEqual(value["responseDigest"], digest({key:item for key,item
            in value.items() if key != "responseDigest"}))
        self.assertEqual(before, (m.AiBusinessV4Chunk.objects.count(),
            m.AiBusinessV4ToolReceipt.objects.count()))
        row = table["rows"][0]
        exact = owner.read_row(self.parent.id, KEYS, "current", VIEW,
            row["rowIndex"], row["id"], self.principal, enabled=True)
        self.assertEqual(exact["row"], row)

    def test_default_closed_duplicate_or_missing_window_and_wrong_row_refuse(self):
        with patch.object(owner.replay, "inspect",
                side_effect=AssertionError("closed must not read")) as inspect:
            with self.assertRaises(AiError):
                owner.page(self.parent.id, self.request(), self.principal)
            inspect.assert_not_called()
        self.complete()
        for keys in ({**KEYS, "previous":KEYS["current"]},
                {"current":KEYS["current"], "previous":KEYS["previous"]},
                {**KEYS, "yearAgo":"other-run-source"}):
            with self.subTest(keys=keys), self.assertRaises(AiError):
                owner.page(self.parent.id, self.request(sourceKeys=keys),
                    self.principal, enabled=True)
        value = owner.page(self.parent.id, self.request(), self.principal,
            enabled=True)
        row = value["table"]["rows"][0]
        with self.assertRaises(AiError):
            owner.read_row(self.parent.id, KEYS, "current", VIEW,
                row["rowIndex"], "0"*64, self.principal, enabled=True)

    def test_second_pass_tail_tamper_and_account_revocation_fail_closed(self):
        self.complete()
        original = owner.capacity_owner._receipt_pages
        def altered(*args, **kwargs):
            packets = list(original(*args, **kwargs))
            for index, packet in enumerate(packets):
                if index == len(packets)-1:
                    page = json.loads(packet["rawPage"])
                    page["items"][0]["dimensions"]["attributedSkuId"] = "FORGED"
                    packet = {**packet, "rawPage":canonical(page).encode("utf-8")}
                yield packet
        with patch.object(owner.capacity_owner, "_receipt_pages", altered), \
                self.assertRaises(AiError):
            owner.page(self.parent.id, self.request(), self.principal,
                enabled=True)
        def revoke(event):
            if (type(event) is dict and event.get("stage") ==
                    "v4_owning_capacity" and event.get("sequence") == 1):
                AppUser.objects.filter(email=self.principal.email).update(
                    status="inactive")
        with self.assertRaises(AiError):
            owner.page(self.parent.id, self.request(), self.principal,
                enabled=True, checkpoint=revoke)

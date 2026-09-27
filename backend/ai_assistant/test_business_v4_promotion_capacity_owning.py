"""Target isolated PG checks for the closed owning capacity bridge."""
from copy import deepcopy
from unittest.mock import patch

from django.test import TestCase
from access_control.models import AppUser

from . import business_v4_promotion_capacity_owning as owning
from . import business_v4_promotion_replay as replay
from . import models as m
from . import test_business_v4_netshop_promotion as fixture
from .policy import AiError, digest


class BusinessV4PromotionCapacityOwningTests(TestCase):
    setUp = fixture.BusinessV4NetshopPromotionTests.setUp
    owner = fixture.BusinessV4NetshopPromotionTests.owner
    advance = fixture.BusinessV4NetshopPromotionTests.advance
    three_window_run = fixture.BusinessV4NetshopPromotionTests.three_window_run

    def complete(self):
        first = self.advance(1, "capacity-current-first")
        self.advance(first["runVersion"], "capacity-current-second")

    def test_default_closed_and_single_window_internal_audit_not_upstream(self):
        with self.assertRaises(AiError):
            owning.measure_window(self.parent.id, "promotion-current",
                self.principal)
        self.complete()
        before = (m.AiBusinessV4Chunk.objects.count(),
            m.AiBusinessV4ToolReceipt.objects.count())
        value = owning.measure_window(self.parent.id, "promotion-current",
            self.principal, enabled=True)
        self.assertEqual((value["measuredPageCount"],
            value["measuredRowCount"]), (2, 101))
        self.assertTrue(value["internalSignedToolAuditBound"])
        self.assertTrue(value["requestCursorAuditBound"])
        self.assertTrue(value["databasePersistedCanonicalBytesMeasured"])
        self.assertFalse(value["rawUpstreamHttpBytesVerified"])
        self.assertFalse(value["upstreamSignatureVerified"])
        self.assertFalse(value["v4PlanMeasurementAvailable"])
        self.assertFalse(value["sealerOrReportAuthorityGranted"])
        self.assertEqual(value["owningCapacityDigest"], digest({key: item
            for key, item in value.items() if key != "owningCapacityDigest"}))
        self.assertEqual((m.AiBusinessV4Chunk.objects.count(),
            m.AiBusinessV4ToolReceipt.objects.count()), before)

    def test_three_exact_windows_share_run_but_do_not_verify_finance(self):
        self.three_window_run()
        version = 1
        for window in ("previous", "yearAgo", "current"):
            key = f"promotion-{window}"
            first = self.advance(version, f"capacity-{window}-first", key)
            second = self.advance(first["runVersion"],
                f"capacity-{window}-second", key)
            version = second["runVersion"]
        value = owning.measure_three_windows(self.parent.id, self.principal,
            enabled=True)
        self.assertEqual(set(value["windows"]), {"current", "previous", "yearAgo"})
        self.assertEqual(value["promotionTotals"]["rows"], 303)
        self.assertEqual(value["promotionTotals"]["pages"], 6)
        self.assertFalse(value["financeCapacityVerified"])
        self.assertFalse(value["shopSalesSkuSpuMarketB2bCapacityVerified"])
        self.assertFalse(value["v4PlanMeasurementAvailable"])
        self.assertFalse(value["sealerOrReportAuthorityGranted"])
        self.assertFalse(m.AiBusinessV4Seal.objects.filter(run_id=self.parent.id).exists())

    def test_wrong_signed_request_or_second_pass_proof_refuses(self):
        self.complete()
        second = m.AiBusinessV4ToolReceipt.objects.get(run=self.parent,
            source=self.sources["promotion-current"], sequence=2)
        class WrongAudit:
            def __get__(self, instance, owner):
                if instance is None:
                    return self
                return ('{"argumentsDigest":"' + '0' * 64 + '"}'
                    if instance.id == second.audit_id else
                    instance.__dict__.get("arguments_json"))
            def __set__(self, instance, value):
                instance.__dict__["arguments_json"] = value
        with patch.object(m.AiToolAuditLogs, "arguments_json", WrongAudit()), \
                self.assertRaises(AiError):
            owning.measure_window(self.parent.id, "promotion-current",
                self.principal, enabled=True)
        genuine = replay.inspect(self.parent.id, "promotion-current",
            self.principal)
        forged = deepcopy(genuine)
        forged["receiptChainDigest"] = "0" * 64
        forged["proofDigest"] = digest({key: value for key, value
            in forged.items() if key != "proofDigest"})
        with patch.object(owning.replay, "inspect", return_value=forged), \
                self.assertRaises(AiError):
            owning.measure_window(self.parent.id, "promotion-current",
                self.principal, enabled=True)

    def test_account_revoked_between_pages_refuses_return(self):
        self.complete()
        def revoke(event):
            if (type(event) is dict and event.get("stage") ==
                    "v4_owning_capacity" and event.get("sequence") == 1):
                AppUser.objects.filter(email=self.principal.email).update(
                    status="inactive")
        with self.assertRaises(AiError):
            owning.measure_window(self.parent.id, "promotion-current",
                self.principal, enabled=True, checkpoint=revoke)

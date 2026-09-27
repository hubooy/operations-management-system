"""Tariff candidates preserve cost maths while all authority stays closed."""
from copy import deepcopy
from unittest import TestCase

from business_analysis.contracts import AnalysisContractError, digest
from business_analysis.test_market_model_cost_envelope import tariff, jobs
from . import business_market_v2_cost_candidate as candidate


def model(**changes):
    value={"id":"configured-model","version":3,"status":"enabled",
        "modelType":"text","protocol":"openai_compatible",
        "maxTokens":128000,"maxToolRounds":6,"maxTotalToolCalls":12}
    return {**value,**changes}


def build(**changes):
    args={"at_utc":"2026-09-25T01:00:00Z",
        "cap_claim_cents":1200,"approval_claim_digest":"b"*64}
    return candidate.build("c"*64,changes.pop("model",model()),
        changes.pop("tariff",tariff()),changes.pop("jobs",jobs()),
        **{**args,**changes})


class MarketV2CostCandidateTests(TestCase):
    def test_fixed_five_agent_requirement_is_not_funds_or_approval(self):
        value=build()
        self.assertEqual(value["requiredCents"],1200)
        self.assertEqual(value["reservedCents"],0)
        self.assertEqual(value["candidateDigest"],digest({key:item
            for key,item in value.items() if key!="candidateDigest"}))
        self.assertEqual(value["status"],"pending_rate_and_approval_verification")
        for field in ("tariffAuthorityVerified","humanApprovalAuthorityVerified",
                "extraChargeCategoryCoverageVerified",
                "currencyConversionVerified","fundsReserved",
                "providerCallsAllowed"):
            self.assertFalse(value[field])

    def test_unknown_price_fx_extra_charges_or_model_change_fail_closed(self):
        for changed in ({"tariff":tariff(currency="USD")},
                {"tariff":tariff(inputNanoYuanPerMillionTokens=0)},
                {"tariff":tariff(modelId="other")},
                {"cap_claim_cents":1199},
                {"chargeable_tools":True},
                {"model":model(status="disabled")},
                {"model":model(maxTokens=4096)},
                {"model":model(maxToolRounds=1)},
                {"model":model(maxTotalToolCalls=4)}):
            with self.subTest(changed=changed), self.assertRaises(
                    AnalysisContractError):
                build(**changed)
        changed=deepcopy(tariff()); changed["extraFeeNanoYuan"]=1
        with self.assertRaises(AnalysisContractError):
            build(tariff=changed)

from copy import deepcopy
from unittest import TestCase
from .budget import calculate
from .budget_offline import payload, render
from .contracts import AnalysisContractError
from .test_budget import fixture


class OfflineBudgetTests(TestCase):
    def test_cached_scenario_must_match_the_verified_baseline(self):
        plan, baselines = fixture()
        result = calculate(plan, baselines)
        value = payload(result, "report-1")
        self.assertEqual(value["expected"]["scenarios"][0]["summary"]["projectedAttributedGmvCents"], 45000)
        changed = deepcopy(result)
        changed["scenarios"][0]["rows"][0]["budgetCents"] += 1
        with self.assertRaises(AnalysisContractError): payload(changed, "report-1")
        self.assertEqual(result["scenarios"][0]["rows"][0]["budgetCents"], 6000)

    def test_source_text_is_inert_json_not_script_or_html(self):
        plan, baselines = fixture()
        attack = '</script><script>window.attacked=true</script>'
        plan["scenarios"][0]["name"] = attack
        baselines[0]["entity"]["skuId"] = attack
        document = render(payload(calculate(plan, baselines), "report-1"))
        self.assertNotIn(attack, document)
        self.assertEqual(document.count("</script>"), 2)
        self.assertIn(r"\u003c/script>", document)

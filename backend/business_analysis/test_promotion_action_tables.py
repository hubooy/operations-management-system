"""Pure projection tests: approved words are retained, never executed."""

from copy import deepcopy
import unittest

from ai_assistant.business_promotion_content_contract import prepare
from ai_assistant.test_business_promotion_content_contract import fixture

from .contracts import AnalysisContractError, canonical, digest
from .promotion_action_tables import KEY, project


def _fact(*, keyword="制冰机", sku="SKU-1", number=1200, row="a" * 64,
          job="job-4"):
    return {"reference": {"kind": "promotion_keyword_sku", "sourceKey": "ads",
        "baselineKey": "ads-prior", "view": "keyword_sku", "rowIndex": 0,
        "rowId": row, "tableBindingDigest": "b" * 64,
        "metric": "spendCents", "field": "value"},
        "value": number, "partial": False, "reportId": "report-1", "jobId": job,
        "entity": {"platform": "jd", "shopName": "测试店", "keyword": keyword,
            "promotedSkuId": sku},
        "identityQualified": sku is not None, "actionableKeywordSku": sku is not None,
        "verification": {"numericReferenceVerified": True,
            "referenceReadVerified": True, "completeAgentReadingVerified": True,
            "completedAgentReadVerified": True}}


def _action(facts, *, kind="action", finding_id="f-1"):
    value = {"id": finding_id, "kind": kind, "title": "词货投放需调整",
        "explanation": "费用和转化应继续观察", "facts": facts}
    if kind == "action":
        value["action"] = {"object": "制冰机词货", "change": "降低该词出价",
            "prerequisites": "确认归因成熟", "successMetric": "投入产出比改善",
            "observationDays": 7, "rollback": "连续三天恶化则停止",
            "priority": "high", "ownerRole": "推广运营", "budgetImpact": "待测算"}
    return value


def _shop_fact(job):
    return {"reference": {"sourceKey": "ads", "dimension": "shop", "rowIndex": 0,
        "rowId": "c" * 64, "metric": "spendCents", "field": "value"},
        "value": 100, "partial": False, "entity": {"platform": "jd", "shopName": "测试店"},
        "reportId": "report-1", "jobId": job,
        "verification": {"numericReferenceVerified": True,
            "completedAgentReadVerified": True}}


def _approved(*findings):
    binding, content = fixture(approved=True)
    content["diagnosis"]["findings"] = list(findings)
    return prepare(binding, content).value


class PromotionActionTableTests(unittest.TestCase):
    def test_only_approved_action_has_one_row_with_exact_words_and_pointer(self):
        fact = _fact()
        value = _approved(_action([fact]), _action([fact], kind="observation", finding_id="f-2"))
        table = project(value)
        self.assertEqual((table.key, table.row_count), (KEY, 1))
        row = tuple(table.rows)[0]
        cells = dict(zip((column.key for column in table.columns), row))
        self.assertEqual(cells["change"], "降低该词出价")
        self.assertEqual(cells["budgetImpact"], "待测算")
        self.assertEqual(cells["budgetImpactStatus"], "待核（未与预算模型逐项绑定）")
        self.assertEqual(cells["observationDays"], 7)
        self.assertEqual(cells["objectType"], "关键词×推广SKU")
        self.assertEqual(cells["objectId"], canonical(fact["entity"]))
        self.assertEqual(cells["actionStatus"], "建议待人工执行")
        self.assertEqual(cells["citationPointers"], canonical([fact["reference"]]))
        self.assertEqual(cells["verifiedNumbers"], canonical([{"reference": fact["reference"],
            "metric": "spendCents", "field": "value", "value": 1200, "partial": False}]))
        self.assertEqual(cells["rowDigest"], digest(list(row[:-1])))
        self.assertIn("未执行", table.note)

    def test_missing_or_conflicting_identity_keeps_each_original_action_row(self):
        missing = _fact(sku=None)
        other = _fact(sku="SKU-2", row="c" * 64)
        candidate_missing = {"reference": {"candidateId": "d" * 64,
            "screening": {"id": "screen-1"}, "row": {"id": "source-row"},
            "evidenceBinding": {"reportId": "report-1"}, "role": "report"},
            "metric": "spendCents", "field": "value", "value": 0,
            "entity": {"platform": "jd", "shopName": "测试店",
                "skuId": None}, "identityQualified": True,
            "verification": {"candidateNumberVerified": True,
                "completedAgentReadVerified": True}}
        value = _approved(_action([missing], finding_id="missing"),
            _action([_fact(), other], finding_id="conflict"),
            _action([candidate_missing], finding_id="candidate-missing"),
            _action([_fact()], finding_id="retained"))
        table = project(value)
        self.assertEqual(table.row_count, 4)
        self.assertIn("report:missing", table.note)
        self.assertIn("report:conflict", table.note)
        self.assertIn("report:candidate-missing", table.note)
        rows = {row[1]: dict(zip((column.key for column in table.columns), row))
                for row in table.rows}
        self.assertEqual(set(rows), {"missing", "conflict", "candidate-missing", "retained"})
        for key in ("missing", "conflict", "candidate-missing"):
            self.assertEqual(rows[key]["objectType"], "待核")
            self.assertIsNone(rows[key]["objectId"])
            self.assertEqual(rows[key]["actionStatus"], "待核身份不可执行")
            self.assertEqual(rows[key]["change"], "降低该词出价")
            self.assertTrue(rows[key]["citationPointers"])
        self.assertEqual(rows["missing"]["verifiedNumbers"], canonical([{
            "reference": missing["reference"], "metric": "spendCents",
            "field": "value", "value": 1200, "partial": False}]))
        self.assertEqual(rows["retained"]["actionStatus"], "建议待人工执行")

    def test_all_specialist_action_roles_and_stable_digest(self):
        value = _approved(_action([_fact()]))
        for index, role in enumerate(("commerce", "promotion", "market_b2b")):
            value["content"]["professionalAnalyses"][role]["findings"] = [
                _action([_fact(job="job-1") if role == "promotion"
                         else _shop_fact("job-" + str(index))], finding_id="a-" + role)]
        value = prepare(value["binding"], value["content"]).value
        first, second = project(value), project(value)
        self.assertEqual(first.rows, second.rows)
        self.assertEqual(first.row_count, 4)
        self.assertEqual({row[0] for row in first.rows},
            {"report", "commerce", "promotion", "market_b2b"})

    def test_invalid_citation_or_shape_fails_instead_of_zero_or_guess(self):
        for mutate in (
            lambda f: f["reference"].update(rowId="short"),
            lambda f: f["reference"].update(sourceKey="other-shop"),
            lambda f: f.update(reportId="other-report"),
            lambda f: f.update(jobId="other-job"),
            lambda f: f.update(value=None),
            lambda f: f["verification"].update(numericReferenceVerified=False),
            lambda f: f["verification"].update(completedAgentReadVerified=False),
            lambda f: f.pop("reference"),
        ):
            fact = _fact(); mutate(fact)
            with self.subTest(mutate=mutate), self.assertRaises(AnalysisContractError):
                project(_approved(_action([fact])))
        no_review = deepcopy(_approved(_action([_fact()])))
        no_review["binding"]["humanReview"] = {"status": "pending", "reviewDigest": None}
        no_review = prepare(no_review["binding"], no_review["content"]).value
        with self.assertRaises(AnalysisContractError): project(no_review)
        malformed = _approved(_action([_fact()]))
        malformed["content"]["diagnosis"]["findings"][0]["action"]["priority"] = []
        malformed = prepare(malformed["binding"], malformed["content"]).value
        with self.assertRaises(AnalysisContractError): project(malformed)


if __name__ == "__main__":
    unittest.main()

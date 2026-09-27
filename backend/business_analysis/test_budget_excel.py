from copy import deepcopy
import io
import random
import unittest
import xml.etree.ElementTree as ET
import zipfile

from . import budget_excel as excel
from .budget import calculate
from .budget_offline import payload
from .report_files import Column, Table, write_pair, NS
from .test_budget import fixture


class ExcelBudgetTests(unittest.TestCase):
    def test_formula_v2_preserves_values_and_limits_with_native_safe_remainders(self):
        from .contracts import AnalysisContractError
        plan, bases = fixture()
        plan['totalBudgetCents'] = 10**12
        plan['reserveCents'] = 0
        for target in plan['targets']:
            target['maxBudgetCents'] = 10**12
        for base in bases:
            base['metrics'].update(spendCents=9999999999999, reportedGmvCents=9999999999999)
        model = self.model(plan, bases)
        old, old_proof = excel.build(model, excel.TITLES)
        explicit, proof = excel.build(model, excel.TITLES, formula_version=1)
        new, new_proof = excel.build(model, excel.TITLES, formula_version=2)
        self.assertEqual(old, explicit)
        self.assertEqual(old_proof, proof)
        self.assertEqual(old_proof, new_proof)
        replaced = 0
        for before, after in zip(old, new):
            self.assertEqual(before.cells.keys(), after.cells.keys())
            for key, (value, formula, style) in before.cells.items():
                updated = after.cells[key]
                self.assertEqual((value, style), (updated[0], updated[2]))
                if formula and 'MOD(' in formula:
                    replaced += 1
                    self.assertNotIn('MOD(', updated[1])
                    self.assertIn('QUOTIENT(', updated[1])
                else:
                    self.assertEqual(formula, updated[1])
        self.assertGreater(replaced, 10)
        self.assertEqual(new_proof['integerProductLimit'], 99999999999999)
        for version in (True, None, 0, 3, '2'):
            with self.assertRaises(AnalysisContractError):
                excel.build(model, excel.TITLES, formula_version=version)

    def model(self, plan=None, bases=None):
        default_plan, default_bases = fixture()
        return payload(calculate(plan or default_plan, bases or default_bases), 'synthetic')

    def test_exact_allocation_matches_independent_iterative_solver(self):
        rng = random.Random(140916)
        for iteration in range(110):
            plan, bases = fixture()
            count = 100 if iteration == 109 else rng.randint(1, 12)
            target, base = deepcopy(plan['targets'][0]), deepcopy(bases[0])
            plan['targets'], bases = [], []
            for i in range(count):
                item = {**target, 'rowId': f'{i:064x}', 'rowIndex': i, 'weight': rng.randint(1, 10000),
                        'minBudgetCents': rng.randint(0, 1000), 'maxBudgetCents': rng.randint(1000, 100000)}
                plan['targets'].append(item)
                bases.append({**base, 'rowId': item['rowId']})
            minimum = sum(t['minBudgetCents'] for t in plan['targets'])
            plan['totalBudgetCents'] = max(1, minimum+rng.randint(0, 200000))
            plan['reserveCents'] = 0
            model = self.model(plan, bases)
            sheets, _ = excel.build(model, excel.TITLES)
            self.assertEqual([sheets[1].cells[i+5,27][0] for i in range(count)],
                             [r['budgetCents'] for r in calculate(plan,bases)['scenarios'][0]['rows']])

    def test_missing_zero_and_precision_limit_are_distinct(self):
        for mode in ('date', 'missing', 'zero', 'unknown_margin', 'zero_margin', 'precision'):
            plan, bases = fixture()
            if mode == 'date': bases[0]['datesPresent'] = False
            if mode == 'missing': bases[0]['metrics']['reportedGmvCents'] = None
            if mode == 'zero': bases[0]['metrics']['reportedOrderLines'] = 0
            if mode.endswith('margin'): plan['scenarios'][0]['contributionMarginBps'] = None if mode.startswith('unknown') else 0
            if mode == 'precision':
                bases[0]['metrics'].update(spendCents=999983,reportedGmvCents=999999999989)
                plan['scenarios'][0].update(cpcFactorBps=10007,orderRateFactorBps=10009,orderValueFactorBps=10037)
            sheets, proof = excel.build(self.model(plan,bases), excel.TITLES)
            row = sheets[2].cells
            if mode in ('date', 'missing', 'zero', 'precision'):
                self.assertIsNone(row[5,10][0], mode)
                self.assertGreater(proof['initialUnmeasurableTargets'],0)
            if mode == 'unknown_margin': self.assertIsNone(row[5,12][0])
            if mode == 'zero_margin': self.assertEqual(row[5,12][0],-6000)

    def test_native_sheets_preserve_original_rows_formulas_and_blank_input_style(self):
        plan, bases = fixture(); plan['scenarios'][0]['contributionMarginBps'] = None
        model = self.model(plan,bases)
        outputs=[]
        for enabled in (False, True):
            xlsx,html=io.BytesIO(),io.BytesIO()
            proof=write_pair(xlsx,html,title='合成',metadata={},tables=[Table('fixed',excel.TITLES[0],'固定原表',
                (Column('value','原值','integer'),),[[123]],1)],excel_budget=model if enabled else None)
            outputs.append((xlsx.getvalue(),proof))
        self.assertEqual(outputs[0][1]['tables'],outputs[1][1]['tables'])
        with zipfile.ZipFile(io.BytesIO(outputs[0][0])) as old, zipfile.ZipFile(io.BytesIO(outputs[1][0])) as new:
            self.assertEqual(old.read('xl/worksheets/sheet1.xml'),new.read('xl/worksheets/sheet1.xml'))
            main=ET.fromstring(new.read('xl/worksheets/sheet2.xml'))
            ns={'x':NS}
            blank=main.find('.//x:c[@r="F16"]',ns)
            self.assertEqual(blank.get('s'),'6');self.assertIsNone(blank.find('x:v',ns))
            self.assertIsNotNone(main.find('x:sheetProtection',ns))
            self.assertIsNotNone(main.find('.//x:dataValidation[@sqref="F16"]',ns))
            self.assertEqual(len(ET.fromstring(new.read('xl/styles.xml')).find('x:cellXfs',ns)),7)
            self.assertEqual(outputs[1][1]['budgetCalculator']['sheets'][0],excel.TITLES[0]+'_2')
            formulas=new.read('xl/worksheets/sheet3.xml').decode()
            self.assertIn("'预算试算_可编辑_2'!",formulas)
            self.assertNotIn('externalLink', ''.join(new.namelist()))

    def test_mixed_reporting_bases_do_not_combine(self):
        plan,bases=fixture(); bases[0]['source']='jd-gross';bases[1]['source']='tmall-net'
        sheets,_=excel.build(self.model(plan,bases),excel.TITLES)
        self.assertEqual(sheets[0].cells[7,5][0],'不可合计或不可测算')
        self.assertEqual(sheets[0].cells[8,5][0],'不可合计或不可测算')

    def test_baseline_precision_status_preserves_usable_forecast_and_is_visible_on_main(self):
        plan, bases = fixture()
        plan['horizonDays'] = 93
        bases[0]['days'] = 1
        bases[0]['metrics'].update(spendCents=9999999999999, reportedGmvCents=9999999999999)
        sheets, proof = excel.build(self.model(plan, bases), excel.TITLES)
        self.assertEqual(sheets[2].cells[5,10][0], 6000)
        self.assertIsNone(sheets[2].cells[5,14][0])
        self.assertIsNone(sheets[2].cells[5,15][0])
        self.assertEqual(sheets[2].cells[5,21][0], '基期对比需高精度复算')
        self.assertEqual(sheets[2].cells[5,16][0], '假设情景；基期对比需高精度复算')
        self.assertEqual(sheets[0].cells[27,8][0], sheets[2].cells[5,16][0])
        self.assertEqual(proof['initialUnmeasurableTargets'], 0)
        self.assertEqual(sheets[0].row_heights[27], 56)
        plan['horizonDays'] = 7
        sheets, _ = excel.build(self.model(plan, bases), excel.TITLES)
        self.assertEqual(sheets[2].cells[5,21][0], '基期对比可测算')
        self.assertEqual(sheets[0].cells[27,8][0], '假设情景')
        bases[0]['datesPresent'] = False
        sheets, _ = excel.build(self.model(plan, bases), excel.TITLES)
        self.assertEqual(sheets[2].cells[5,21][0], '基期对比不可测算')
        self.assertEqual(sheets[0].cells[27,8][0], '不可测算')

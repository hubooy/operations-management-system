// Independent evaluator for synthetic XLSX fixtures. Supply the bundled runtime
// dependency directory; product generation has no dependency on this evaluator.
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';

const root=path.resolve(process.argv[2]);
const require=createRequire(path.join(path.resolve(process.argv[3]),'__budget_qa__.cjs'));
const { FileBlob,SpreadsheetFile }=await import(pathToFileURL(require.resolve('@oai/artifact-tool')).href);
const expected=JSON.parse(await fs.readFile(path.join(root,'expected.json'),'utf8'));
let checked=0;
for(const test of expected.cases){
  const book=await SpreadsheetFile.importXlsx(await FileBlob.load(path.join(root,test.file)));
  const sheets=expected.sheets.map(name=>book.worksheets.getItem(name));
  for(const [index,address,value] of test.edits) sheets[index].getRange(address).values=[[value]];
  await book.recalculate();
  const value=(index,address)=>sheets[index].getRange(address).values[0][0];
  assert.equal(book.worksheets.getItem('原报告固定数值').getRange('A4').values[0][0],45000);
  if(test.invalid){
    assert.equal(value(0,'E10'),'请检查参数及最低预算合计',test.name);
    assert.equal(value(0,'E5'),'');assert.equal(value(2,'P5'),'参数无效');
  }else{
    assert.equal(value(0,'E10'),'参数有效',test.name);
    assert.equal(value(0,'E5'),test.allocation.allocatedCents/100,test.name);
    assert.equal(value(0,'E6'),test.allocation.unallocatedCents/100,test.name);
    for(let i=0;i<test.rows.length;i++){
      const row=test.rows[i],r=i+5;
      const baselinePrecision=(test.baselinePrecisionTargets||[]).includes(i);
      for(const [c,key] of Object.entries({B:'budgetCents',J:'projectedAttributedGmvCents',K:'projectedRoas',L:'assumedContributionAfterAdCents',M:'reviewAfterSpendCents',N:'equivalentBaselineSpendCents',O:'budgetChangeCents'})){
        const actual=value(2,c+r);
        if(test.precision && i===0 && ['J','K','L'].includes(c)){assert.equal(actual,'',test.name+' '+c+r);continue;}
        if(baselinePrecision && ['N','O'].includes(c)){assert.equal(actual,'',test.name+' '+c+r);continue;}
        assert.equal(actual===''?null:actual,row[key],test.name+' '+c+r+' '+key);checked++;
      }
      const status=(test.precision&&i===0?'需高精度复算':
        {unavailable:'不可测算',low_sample_scenario:'低样本假设',assumption_scenario:'假设情景'}[row.status])+(baselinePrecision?'；基期对比需高精度复算':'');
      assert.equal(value(2,'P'+r),status,test.name);
      assert.equal(value(0,'H'+(i+27)),status,test.name+' main status');
      assert.equal(value(2,'U'+r),baselinePrecision?'基期对比需高精度复算':row.equivalentBaselineSpendCents===null?'基期对比不可测算':'基期对比可测算',test.name+' baseline status');
    }
    for(const [address,key] of [['E7','projectedAttributedGmvCents'],['E8','assumedContributionAfterAdCents']]){
      const v=test.summary[key];
      assert.equal(value(0,address),test.precision||v===null?'不可合计或不可测算':v/100,test.name+' '+address);
    }
    if(['original','baseline_precision'].includes(test.name)){
      const image=await book.render({sheetName:expected.sheets[0],range:'A1:H29',scale:1.3,format:'png'});
      await fs.writeFile(path.join(root,test.name==='original'?'xlsx-model.png':'xlsx-baseline-precision.png'),new Uint8Array(await image.arrayBuffer()));
    }
  }
  console.log(JSON.stringify({case:test.name,passed:true}));
}
console.log(JSON.stringify({cases:expected.cases.length,comparedValues:checked,independentRecalculation:true,rendered:true}));

import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import vm from "node:vm";
import { pathToFileURL } from "node:url";
import { chromium } from "playwright-core";

const directory = path.resolve(process.argv[2] || ".runtime/business-budget-offline");
const cases = JSON.parse(await fs.readFile(path.join(directory, "cases.json"), "utf8"));
const context = vm.createContext({});
vm.runInContext(await fs.readFile(path.join(directory, "engine.js"), "utf8"), context);
for (const [i, item] of cases.entries()) {
  const result = context.calculateOfflineBudget(structuredClone(item.plan), item.baselines);
  assert.deepEqual(JSON.parse(JSON.stringify(result)), item.expected, `Python/BigInt case ${i}`);
}
for (const [key, value] of [["totalBudgetCents", true], ["reserveCents", 10**12+1], ["observationDays", 100], ["minimumClicks", .5]]) {
  const item = cases.at(-1), plan = { ...item.plan, [key]: value };
  assert.throws(() => context.calculateOfflineBudget(plan, item.baselines));
}
const extreme = structuredClone(cases.at(-1)); extreme.baselines[0].metrics.clicks = 10**15-1;
assert.throws(() => context.calculateOfflineBudget(extreme.plan, extreme.baselines), /离线精确数值容量/);
const browser = await chromium.launch({ executablePath: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", headless: true });
const page = await browser.newPage({ viewport: { width: 1300, height: 950 }, acceptDownloads: true });
const errors = [], external = [];
page.on("pageerror", e => errors.push(String(e)));
page.on("request", r => { if (/^https?:/.test(r.url())) external.push(r.url()); });
try {
  await page.goto(pathToFileURL(path.join(directory, "report.html")).href);
  const calculator = page.locator("#offline-budget");
  await calculator.getByText("原报告参数 · 初值已与服务端逐项核对", { exact: true }).waitFor();
  const original = await page.locator("#report-data").textContent();
  await calculator.getByLabel("预算上限（元）", { exact: true }).fill("150.50");
  await calculator.getByLabel("假设贡献率（%，未知留空）", { exact: true }).first().fill("");
  await calculator.getByRole("button", { name: "按新参数试算", exact: true }).click();
  await calculator.getByText(/本地试算 v1/).waitFor();
  const downloaded = page.waitForEvent("download");
  await calculator.getByRole("button", { name: "下载本次试算 JSON" }).click();
  const download = await downloaded, file = path.join(directory, "what-if.json"); await download.saveAs(file);
  const result = JSON.parse(await fs.readFile(file, "utf8"));
  assert.equal(result.reviewStatus, "unreviewed_local_scenario");
  assert.equal(result.reportId, "synthetic-report");
  assert.equal(result.result.allocation.allocatedCents, 14050);
  assert.equal(result.result.scenarios[0].summary.projectedAttributedGmvCents, 70250);
  assert.equal(result.result.scenarios[0].summary.assumedContributionAfterAdCents, null);
  assert.equal(await page.locator("#report-data").textContent(), original);
  await calculator.screenshot({ path: path.join(directory, "calculator.png") });
  await calculator.getByText("对象预算边界与权重", { exact: true }).click();
  await calculator.getByLabel("权重", { exact: true }).first().fill("1");
  await calculator.getByRole("button", { name: "按新参数试算", exact: true }).click();
  const revisedDownload = page.waitForEvent("download");
  await calculator.getByRole("button", { name: "下载本次试算 JSON" }).click();
  await (await revisedDownload).saveAs(path.join(directory, "weighted.json"));
  const weighted = JSON.parse(await fs.readFile(path.join(directory, "weighted.json"), "utf8"));
  assert.equal(weighted.localRevision, 2);
  assert.deepEqual(weighted.result.scenarios[0].rows.map(r => r.budgetCents), [7025, 7025]);
  await calculator.getByLabel("预留预算（元）", { exact: true }).fill("200.00");
  assert.equal(await calculator.getByRole("button", { name: "下载本次试算 JSON" }).isDisabled(), true);
  await calculator.getByRole("button", { name: "按新参数试算", exact: true }).click();
  await calculator.getByText("参数无效；未产生试算结果", { exact: true }).waitFor();
  assert.equal(await calculator.locator("table").count(), 0);
  await calculator.getByRole("button", { name: "恢复原报告参数" }).click();
  await calculator.getByText("原报告参数 · 初值已与服务端逐项核对", { exact: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await calculator.screenshot({ path: path.join(directory, "mobile.png") });
  const html = await fs.readFile(path.join(directory, "report.html"), "utf8");
  const pattern = /(<script type="application\/json" id="budget-data">)([\s\S]*?)(<\/script>)/;
  const source = JSON.parse(html.match(pattern)[2]);
  const attack = '</script><script>window.attacked=true</script>';
  source.plan.scenarios[0].name = attack; source.baselines[0].entity.skuId = attack;
  const writeVariant = async name => {
    const file = path.join(directory, name);
    await fs.writeFile(file, html.replace(pattern, () => '<script type="application/json" id="budget-data">'+JSON.stringify(source).replaceAll("<", "\\u003c")+'</script>'));
    await page.goto(pathToFileURL(file).href);
  };
  await writeVariant("literal-source.html");
  await calculator.getByText("原报告参数 · 初值已与服务端逐项核对", { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => window.attacked), undefined);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.equal(await calculator.locator("script").count(), 0);
  source.expected.scenarios[0].summary.projectedAttributedGmvCents++;
  await writeVariant("bad-cache.html");
  await calculator.getByRole("alert").getByText("初值核对不一致，禁止试算", { exact: true }).waitFor();
  assert.equal(await calculator.getByRole("button", { name: "按新参数试算", exact: true }).isDisabled(), true);
  assert.equal(await calculator.locator("table").count(), 0);
  assert.deepEqual(errors, []); assert.deepEqual(external, []);
  console.log(JSON.stringify({ status: "passed", exactEngineCases: cases.length, invalidCases: 5, editableCents: true, editableWeights: true, unknownMargin: true, originalPreserved: true, localRevisionDownload: true, invalidClearsOutput: true, mobile: true, sourceTextInert: true, initialMismatchBlocksCalculation: true, externalRequests: external.length }));
} finally { await browser.close(); }

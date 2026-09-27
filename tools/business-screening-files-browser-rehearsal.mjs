// Actual synthetic five-Agent persisted delivery, inspected without network.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { chromium } from "playwright-core";

const directory = path.resolve(process.argv[2]);
const manifest = JSON.parse(await fs.readFile(path.join(directory,"volume-0.json"),"utf8"));
assert.equal(manifest.rendererVersion,6);
assert.equal(manifest.volumeCount,1);
const browser = await chromium.launch({headless:true,executablePath:"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"});
const page = await browser.newPage({viewport:{width:1440,height:1000},acceptDownloads:true});
const errors=[], external=[];
page.on("pageerror",e=>errors.push(String(e)));
page.on("request",r=>{if(/^https?:/.test(r.url()))external.push(r.url());});
try {
  await page.goto(pathToFileURL(path.join(directory,"volume-1.html")).href);
  await page.locator("#tbody tr").first().waitFor();
  const tabs=page.locator("#nav button");
  assert.equal(await tabs.count(),manifest.sourceTableCount);
  assert.match(await tabs.first().innerText(),/经营摘要/);
  assert.match(await page.locator("#tbody").innerText(),/总体判断/);
  let rows=0;
  for(let i=0;i<await tabs.count();i++){
    await tabs.nth(i).click();
    rows+=await page.locator("#tbody tr").count();
  }
  await tabs.first().click();
  await page.locator("#search").fill("总体判断");
  assert.equal(await page.locator("#tbody tr").count(),1);
  await page.locator("#search").fill("");
  const pending=page.waitForEvent("download");
  await page.locator("#export").click();
  await (await pending).saveAs(path.join(directory,"summary.csv"));
  assert.match(await fs.readFile(path.join(directory,"summary.csv"),"utf8"),/总体判断/);
  await page.screenshot({path:path.join(directory,"desktop.png"),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:path.join(directory,"mobile.png"),fullPage:true});
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  const evidence={passed:true,rendererVersion:6,tableCount:await tabs.count(),visibleRowsTraversed:rows,
    summaryFirst:true,search:true,csv:true,mobileNoOverflow:true,errors,external,syntheticOnly:true,nativeExcel:false};
  await fs.writeFile(path.join(directory,"browser-evidence.json"),JSON.stringify(evidence,null,2));
  console.log(JSON.stringify(evidence));
} finally {await browser.close();}

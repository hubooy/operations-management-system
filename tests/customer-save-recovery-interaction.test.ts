import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { build } from "esbuild";
import { chromium } from "playwright-core";

const chrome = process.env.CHROME_PATH ?? "C:/Program Files/Google/Chrome/Application/chrome.exe";
declare global {
  interface Window {
    customerFixture: {mode:string;readFails:boolean;writes:number;reads:string[];release:(()=>void)|null};
    renderCustomer: (end?: string) => void;
  }
}

test("customer save failures survive authoritative refresh; only a confirmed save clears them", {
  skip: !existsSync(chrome), timeout: 60_000,
}, async () => {
  const bundle = await build({ stdin: { contents: `
    import { createRoot } from 'react-dom/client';
    import CustomerServiceView from './app/customer-service-view';
    const root=createRoot(document.getElementById('root'));
    window.renderCustomer=(end='2026-09-15')=>root.render(<CustomerServiceView
      customStartDate="2026-09-01" customEndDate={end}
      currentUser={{role:'admin',email:'fixture@example.invalid',displayName:'Fixture'}} onNavigate={()=>{}} />);
    window.renderCustomer();`, loader: "tsx", resolveDir: fileURLToPath(new URL("../", import.meta.url)) },
    bundle:true,write:false,format:"iife",platform:"browser",jsx:"automatic",
    define:{"process.env.NODE_ENV":'"test"'},
  });
  const browser=await chromium.launch({executablePath:chrome,headless:true});
  try {
    const page=await browser.newPage(); const errors:string[]=[];
    page.on("pageerror",error=>errors.push(error.message));
    await page.route("**/*",route=>route.fulfill({contentType:"text/html",body:'<div id="root"></div>'}));
    await page.goto("https://customer-fixture.invalid/");
    await page.addStyleTag({content:await readFile(new URL("../app/globals.css",import.meta.url),"utf8")});
    await page.evaluate(()=>{
      const item={id:1,shopName:"合成店铺",consultedAt:"2026-09-15 03:00:00",customerId:"fixture",agent:"合成客服",
        matchedSkuId:"fixture",productCategory:"合成类目",productName:"合成商品",matchStatus:"matched",matchConfidence:"exact",
        messageTotalCount:0,messages:[],robotScope:"exclude_robot",problemType:"商品咨询",conversionStatus:"unknown",
        serviceIssues:"",summaryText:"",analysisSource:"manual",analyzedAt:null,version:1};
      const state={item,mode:"503",readFails:false,writes:0,reads:[] as string[],release:null as null|(()=>void)};
      Object.assign(window,{customerFixture:state});
      window.fetch=async(input,init)=>{
        const url=String(input);
        if(url.includes("/analyze"))return Response.json({ready:true});
        if(init?.method==="PATCH"){
          state.writes++;
          if(state.mode==="delayed")await new Promise<void>(resolve=>{state.release=resolve;});
          if(state.mode==="network")throw new TypeError("合成网络断开");
          if(state.mode==="missing-version")return Response.json({});
          if(state.mode==="success"){
            Object.assign(state.item,JSON.parse(String(init.body)),{version:state.item.version+1});
            return Response.json({version:state.item.version});
          }
          return Response.json({error:"合成保存失败"},{status:state.mode==="409"?409:503});
        }
        state.reads.push(url);
        if(state.readFails)return Response.json({error:"合成回读失败"},{status:503});
        return Response.json({items:[state.item],agents:["合成客服"],shops:["合成店铺"],categories:["合成类目"],
          summary:{total:1,matched:1,sessionOnly:0,chatOnly:0},pagination:{page:1,pageSize:30,total:1,returned:1,truncated:false}});
      };
    });
    await page.addScriptTag({content:bundle.outputFiles[0].text});
    const select=page.getByRole("button",{name:"1问题类型",exact:true}); await select.waitFor();
    const change=async()=>{await select.click();await page.getByRole("option",{name:"价格优惠",exact:true}).click();};
    for(const mode of ["503","409","network","missing-version"]){
      const before=await page.evaluate(mode=>{
        const s=window.customerFixture;s.mode=mode;return s.writes;
      },mode);
      await change();
      await page.getByRole("alert").waitFor();
      await page.waitForFunction(()=>!(document.querySelector('[aria-label="1问题类型"]') as HTMLButtonElement)?.disabled);
      assert.match(await select.innerText(),/商品咨询/);
      assert.equal(await page.evaluate(()=>window.customerFixture.writes),before+1);
      await page.getByRole("button",{name:"↻ 刷新数据",exact:true}).click();
      await page.getByRole("button",{name:"↻ 刷新数据",exact:true}).waitFor();
      assert.ok((await page.getByRole("alert").innerText()).length>0);
    }
    await page.evaluate(()=>{const s=window.customerFixture;s.mode="503";s.readFails=true;});
    await change();
    await page.waitForFunction(()=>document.querySelector('[role="alert"]')?.textContent?.includes("合成回读失败"));
    assert.match(await page.getByRole("alert").innerText(),/合成保存失败/);
    assert.match(await select.innerText(),/商品咨询/);
    await page.evaluate(()=>{window.customerFixture.readFails=false;});
    await page.getByRole("button",{name:"↻ 刷新数据",exact:true}).click();
    await page.waitForFunction(()=>!document.querySelector('[role="alert"]')?.textContent?.includes("合成回读失败"));
    assert.match(await page.getByRole("alert").innerText(),/合成保存失败/);
    if (process.env.CUSTOMER_RECOVERY_SCREENSHOT) {
      await page.screenshot({path:process.env.CUSTOMER_RECOVERY_SCREENSHOT,fullPage:true});
    }

    // Ignore a delayed old write's read closure: refresh the current date range.
    await page.evaluate(()=>{window.customerFixture.mode="delayed";});
    await change();
    await page.waitForFunction(()=>window.customerFixture.release!==null);
    await page.evaluate(()=>window.renderCustomer("2026-09-16"));
    await page.waitForFunction(()=>window.customerFixture.reads.at(-1).includes("endDate=2026-09-16"));
    await page.evaluate(()=>window.customerFixture.release!());
    await page.waitForFunction(()=>!(document.querySelector('[aria-label="1问题类型"]') as HTMLButtonElement)?.disabled);
    assert.match(await page.evaluate(()=>window.customerFixture.reads.at(-1)),/endDate=2026-09-16/);
    assert.match(await page.getByRole("alert").innerText(),/合成保存失败/);
    await page.evaluate(()=>{window.customerFixture.mode="delayed";window.customerFixture.release=null;});
    await change();
    await page.waitForFunction(()=>window.customerFixture.release!==null);
    await page.evaluate(()=>window.renderCustomer("2026-09-17"));
    await page.waitForFunction(()=>window.customerFixture.reads.at(-1)!.includes("endDate=2026-09-17"));
    await page.evaluate(()=>{window.customerFixture.mode="success";window.customerFixture.release!();});
    await page.waitForFunction(()=>!document.querySelector('[role="alert"]'));
    assert.match(await select.innerText(),/价格优惠/);
    assert.match(await page.evaluate(()=>window.customerFixture.reads.at(-1)!),/endDate=2026-09-17/);
    assert.deepEqual(errors,[]);
  } finally {await browser.close();}
});

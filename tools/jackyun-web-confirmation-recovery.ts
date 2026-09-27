import { readFile } from "node:fs/promises";
import path from "node:path";
import { homedir } from "node:os";
import { fileURLToPath } from "node:url";
import { readN8nPreflightEvidence } from "./jackyun-preflight-recovery";
import { recoverySha } from "../lib/jackyun/preflight-recovery";
import { auditedWebConfirmation849,inspectWebConfirmationRecovery,publishWebConfirmationRecovery,type WebRecoveryPermit } from "../lib/jackyun/web-session-recovery";
import { readJackyunLoginConfig } from "../lib/jackyun/windows-dpapi";
import { withJackyunRunLock } from "../lib/jackyun/run-lock";
import { launchDedicatedChrome,closeChromeBrowser } from "../lib/jackyun/cdp-client";
import { connectPlaywrightBrowser,PlaywrightPageClient } from "../lib/jackyun/playwright-client";
import { inspectJackyunLoginSurface,submitJackyunDpapiLogin,verifyJackyunBrowserBinding,waitForJackyunDpapiSession } from "../lib/jackyun/dpapi-login";
import { readWebSessionTasks } from "../lib/jackyun/web-session-export";

async function main(){
  const [action,proposalPath,sha]=process.argv.slice(2);
  if(action!=="plan"&&action!=="apply"||action==="plan"&&process.argv.length!==3||action==="apply"&&(!proposalPath||!/^[a-f0-9]{64}$/.test(sha??"")||process.argv.length!==5))throw Error("用法：plan；apply <proposal.json> <approvedSha256>");
  const sourceRoot=path.resolve(path.dirname(fileURLToPath(import.meta.url)),".."),config=await readJackyunLoginConfig(sourceRoot);
  const root=path.dirname(path.dirname(path.resolve(config.profileDirectory)));
  const policy=JSON.parse(await readFile(path.join(root,"config/jackyun-export-first-policy.json"),"utf8"));
  const chromePath=policy.browser.controller.chromePath;
  await withJackyunRunLock({runId:"n8n-export-first-849",purpose:"audited_web_confirmation_recovery",lockDirectory:path.join(root,".runtime/jackyun-automation.lock")},async()=>{
    const health=await fetch("http://127.0.0.1:5791/health",{signal:AbortSignal.timeout(10000)}),h=await health.json() as {ok:boolean;busy:boolean;activeWorkflow:unknown};
    if(!health.ok||!h.ok||h.busy||h.activeWorkflow)throw Error("helper 非空闲。");
    const evidence=readN8nPreflightEvidence(path.join(homedir(),".n8n/database.sqlite"),"849");
    const owned=await launchDedicatedChrome({executablePath:chromePath,profileDirectory:config.profileDirectory,port:config.debuggingPort,startUrl:"https://web.jackyun.com/home/mainframe_web_horizontal.html",headless:true});
    let browser;
    try{
      await verifyJackyunBrowserBinding({chromePath,profileDirectory:config.profileDirectory,port:config.debuggingPort});browser=await connectPlaywrightBrowser(config.debuggingPort);
      const page=browser.contexts()[0].pages()[0];
      const login=await waitForJackyunDpapiSession({inspect:()=>inspectJackyunLoginSurface(page,config.tenantId),submit:()=>submitJackyunDpapiLogin(page,config),initialWaitMs:config.initialWaitMs,afterSubmitWaitMs:config.afterSubmitWaitMs});
      if(login.status!=="authenticated")throw Error("吉客云会话未通过。");
      const client=new PlaywrightPageClient(page,await page.context().newCDPSession(page));
      const tasks=await readWebSessionTasks(client,"combos",auditedWebConfirmation849.exportIntentAt);
      if(!tasks.records.length||tasks.records.some(t=>t.createdAt>=Math.floor(Date.parse(auditedWebConfirmation849.exportIntentAt)/1000)*1000))throw Error("原窗口出现任务，拒绝再次准备导出。");
      const observation={observedAt:new Date().toISOString(),taskIds:tasks.records.map(t=>t.taskId),newestTaskAt:new Date(Math.max(...tasks.records.map(t=>t.createdAt))).toISOString()};
      if(action==="plan"){
        const proposal=await inspectWebConfirmationRecovery(root,evidence,observation,new Date().toISOString());
        console.log(JSON.stringify({proposal,approvedSha256:recoverySha(JSON.stringify(proposal))}));
      }else{
        const {proposal}=JSON.parse(await readFile(proposalPath,"utf8")) as {proposal:WebRecoveryPermit};
        if(proposal.root!==root||Date.now()-Date.parse(proposal.createdAt)>30*60000)throw Error("许可根目录或有效期不符。");
        await publishWebConfirmationRecovery(proposal,evidence,sha);
        console.log(JSON.stringify({status:"web_confirmation_permit_published",runId:proposal.runId}));
      }
    }finally{await browser?.close();if(owned)await closeChromeBrowser(config.debuggingPort);}
  });
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))main().catch(error=>{console.error(error instanceof Error?error.message:"恢复未通过");process.exitCode=1;});

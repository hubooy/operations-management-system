import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { isolatedHelperErrorDiagnostic, spawnIsolatedHelper, type HelperThreadDiagnostic } from "../tools/tmall-isolated-helper";

test("thread diagnostics retain safe error class and source positions without arbitrary text", () => {
  const error = new TypeError("secret=DO_NOT_LOG https://private.invalid/token");
  error.stack = "TypeError: DO_NOT_LOG\n    at secretFunction (D:\\private\\tmall-workflow-helper.mjs:123:7)\n    at https://private.invalid/secret.js:8:9\n    at D:\\private\\coreBundle.js:456:11";
  assert.deepEqual(isolatedHelperErrorDiagnostic(error), {
    name: "TypeError", category: "other", frames: [
      {file:"tmall-workflow-helper.mjs",line:123,column:7},
      {file:"coreBundle.js",line:456,column:11},
    ],
  });
  Object.assign(error,{code:"ERR_WORKER_OUT_OF_MEMORY"});
  assert.equal(isolatedHelperErrorDiagnostic(error).category,"worker_out_of_memory");
  assert.equal(isolatedHelperErrorDiagnostic("DO_NOT_LOG").name,"Error");
});

for (const route of ["/throw", "/crash", "/finish"]) {
  test(`real thread ${route} captures post-ready failure while preserving cleanup semantics`, async () => {
    const diagnostics: HelperThreadDiagnostic[]=[];
    let finished!: (clean:boolean)=>void;
    const finish=new Promise<boolean>(resolve=>{finished=resolve;});
    const child=await spawnIsolatedHelper(fileURLToPath(new URL("fixtures/tmall-isolated-worker.mjs",import.meta.url)),
      {key:"tmall-lili",storeKey:"tmall-lili",workflow:"tmall",executionId:`diagnostic-${route.slice(1)}`},
      finished, diagnostic=>{diagnostics.push(diagnostic);});
    let timer:ReturnType<typeof setTimeout>|undefined;
    try {
      const response=await fetch(`http://127.0.0.1:${child.port}${route}`,{
        headers:{"x-teruisi-helper-slot-token":child.token,connection:"close"},signal:AbortSignal.timeout(5000),
      });
      assert.equal(response.status,200);await response.text();
      const clean=await Promise.race([finish,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(new Error("fixture timeout")),5000);})]);
      assert.equal(clean,route==="/finish");
      assert.ok(!JSON.stringify(diagnostics).includes("DO_NOT_LOG"));
      assert.ok(!JSON.stringify(diagnostics).includes("secret.invalid"));
      if(route==="/finish")assert.deepEqual(diagnostics,[]);
      else {
        const exit=diagnostics.find(d=>d.event==="helper_thread_unclean_exit");
        assert.equal(exit?.exitCode,1);assert.equal(exit.ready,true);
        if(route==="/throw"){
          assert.equal(exit.phase,"master_browser_connect");
          assert.equal(exit.error?.name,"TypeError");
          assert.equal(diagnostics.filter(d=>d.event==="helper_thread_error").length,1);
        } else assert.equal(exit.error,undefined);
      }
    } finally {clearTimeout(timer);await child.stop();}
  });
}

test("diagnostic sink failure cannot prevent the original unclean finish callback",async()=>{
  let finished!:(clean:boolean)=>void;
  const finish=new Promise<boolean>(resolve=>{finished=resolve;});
  const child=await spawnIsolatedHelper(fileURLToPath(new URL("fixtures/tmall-isolated-worker.mjs",import.meta.url)),
    {key:"tmall-lili",storeKey:"tmall-lili",workflow:"tmall",executionId:"sink-failure"},finished,
    ()=>{throw new Error("sink unavailable");});
  let timer:ReturnType<typeof setTimeout>|undefined;
  try{
    await (await fetch(`http://127.0.0.1:${child.port}/throw`,{headers:{"x-teruisi-helper-slot-token":child.token},signal:AbortSignal.timeout(5000)})).text();
    assert.equal(await Promise.race([finish,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(new Error("fixture timeout")),5000);})]),false);
  }finally{clearTimeout(timer);await child.stop();}
});

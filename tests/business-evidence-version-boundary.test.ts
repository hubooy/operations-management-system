import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";

test("legacy evidence metadata tool rejects compact catalogs without changing bounded chunk/table reads", async () => {
  const bundle = await build({entryPoints:["lib/ai/business-evidence.ts"],bundle:true,write:false,platform:"node",format:"esm",
    plugins:[{name:"evidence-boundary",setup(builder) {
      builder.onResolve({filter:/^@\/lib\/(?:django\/ai-service|netshop\/analysis-tool)$/}, args=>({path:args.path,namespace:"boundary"}));
      builder.onLoad({filter:/.*/,namespace:"boundary"}, args=>({loader:"js", contents:args.path.includes("analysis-tool")
        ? `export const requireAnalysisPrincipal = () => {};`
        : `export async function requestDjangoAi(_,input) {
            globalThis.__businessEvidenceCalls.push(input);
            if(input.path.includes('/chunks/')) return {data:{schemaVersion:'business-evidence-slice-v1',items:[]}};
            if(input.path.endsWith('/analysis')) return {data:{rows:[],total:0}};
            const version=input.path.endsWith('/v1')?'business-evidence-v1':input.path.endsWith('/v2')?'business-evidence-v2':'unknown';
            return {data:{item:{plan:{schemaVersion:version}}}};
          }`}));
    }}]});
  const functions = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`) as {
    readBusinessEvidence(args:unknown,principal:unknown):Promise<unknown>;
    readBusinessAnalysisTable(args:unknown,principal:unknown):Promise<unknown>;
  };
  const state = globalThis as unknown as {__businessEvidenceCalls: unknown[]};
  state.__businessEvidenceCalls=[];
  try {
    await functions.readBusinessEvidence({runId:"v1"},{});
    for(const runId of ["v2","unknown"]) await assert.rejects(functions.readBusinessEvidence({runId},{}), (error: unknown) => {
      const value=error as {status:number;code:string}; return value.status===409&&value.code==="conflict";
    });
    assert.deepEqual(await functions.readBusinessEvidence({runId:"v2",sourceKey:"sales",sequence:1},{}),{schemaVersion:"business-evidence-slice-v1",items:[]});
    assert.deepEqual(await functions.readBusinessAnalysisTable({runId:"v2",sourceKey:"sales",dimension:"shop"},{}),{rows:[],total:0});
    assert.equal(state.__businessEvidenceCalls.length,5);
  } finally { Reflect.deleteProperty(state,"__businessEvidenceCalls"); }
});

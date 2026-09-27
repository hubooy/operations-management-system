import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";

test("source named content remains JSON while existing report and asset content stay attachments", async () => {
  const bundle = await build({
    entryPoints: ["lib/ai/django-route.ts"], bundle: true, write: false, platform: "node", format: "esm",
    plugins: [{ name: "source-route-boundaries", setup(builder) {
      builder.onResolve({ filter: /^@\/(?:lib\/(?:auth\/authorization|django\/(?:ai-service|ai-stream)|ai\/page-context)|app\/api\/ai\/route-helpers)$/ }, args => ({ path: args.path, namespace: "boundary" }));
      builder.onLoad({ filter: /.*/, namespace: "boundary" }, args => {
        let contents: string;
        if (args.path.includes("authorization")) contents = `
          export const requireAppPrincipal = async () => ({email:'fixture@example.invalid',role:'admin',scope:null});
          export const requireUnrestrictedDataScope = () => {};`;
        else if (args.path.includes("ai-service")) contents = `
          export const isPublicAiPath = () => true;
          export const requestDjangoAi = async (_, input) => ({status:200,revision:'9', data:
            input.path.includes('/business-evidence/') ? {sourceKey:'content',pageCount:2} :
            {content:'verified-file',mimeType:'text/html',fileName:'report.html'}});`;
        else if (args.path.includes("ai-stream")) contents = `export const requestDjangoAiStream = () => {throw Error('unexpected stream')};`;
        else if (args.path.includes("page-context")) contents = `export const normalizeAiPageContext = value => value;`;
        else contents = `
          export const aiJsonResponse = (data, init) => Response.json(data, init);
          export const aiRouteErrorResponse = error => Response.json({error:String(error)}, {status:500});
          export const readAiJsonObject = request => request.json();
          export const requireAiSameOriginWrite = () => {};`;
        return { contents, loader: "js" };
      });
    } }],
  });
  const route = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`) as {forwardAiRequest(request: Request): Promise<Response>};
  for (const path of ["business-evidence/run-1/sources/content", "business-evidence/run-1/chunks/content"]) {
    const response = await route.forwardAiRequest(new Request("https://fixture.invalid/api/ai/"+path));
    assert.equal(response.status, 200);
    assert.match(response.headers.get("content-type")!, /application\/json/);
    assert.equal(response.headers.has("content-disposition"), false);
    assert.equal(response.headers.has("x-ai-generated"), false);
    assert.deepEqual(await response.json(), {sourceKey:"content",pageCount:2});
  }
  for (const path of ["reports/report-1/content", "space/assets/asset-1/content", "artifacts/artifact-1"]) {
    const response = await route.forwardAiRequest(new Request("https://fixture.invalid/api/ai/"+path));
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("content-type"), "text/html");
    assert.match(response.headers.get("content-disposition")!, /attachment/);
    assert.equal(response.headers.get("x-ai-generated"), path.startsWith("artifacts/") ? null : "true");
    assert.equal(await response.text(), "verified-file");
  }
});

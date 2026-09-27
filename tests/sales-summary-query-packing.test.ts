import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";

test("sales summary forwards 100 codes as one signed query field and preserves other filters", async () => {
  const bundle = await build({
    entryPoints: ["app/api/sales/summary/route.ts"], bundle: true, write: false,
    platform: "node", format: "esm",
    plugins: [{ name: "route-boundaries", setup(builder) {
      builder.onResolve({ filter: /^@\/lib\/(auth\/authorization|django\/sales-gateway)$/ }, args => ({ path: args.path, namespace: "boundary" }));
      builder.onLoad({ filter: /.*/, namespace: "boundary" }, args => ({ contents: args.path.includes("authorization")
        ? `export const requireAppPrincipal = async () => ({role:'admin',scope:null});
           export const requireUnrestrictedDataScope = () => {};
           export const authorizationErrorResponse = () => null;`
        : `export const routeDjangoSalesReadRequest = async ({request,principal}) => Response.json({url:request.url, marker:request.headers.get('x-test'), role:principal.role});`, loader: "js" }));
    } }],
  });
  const route = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`) as {GET(request:Request):Promise<Response>};
  const codes = Array.from({length:100}, (_,i)=>`SKU-${String(i).padStart(5,"0")}`);
  codes[99] += "Z";
  assert.equal(codes.join(",").length,1000);
  const params = new URLSearchParams({range:"custom",startDate:"2026-08-01",endDate:"2026-08-02",category:"净水设备",platform:"京东"});
  for (const code of codes) params.append("productQuery",code);
  assert.ok([...params].length>100);
  const result = await route.GET(new Request("http://example.test/api/sales/summary?"+params,{headers:{"x-test":"preserved"}}));
  assert.equal(result.status,200);
  const forwarded = await result.json();
  const query = new URL(forwarded.url).searchParams;
  assert.deepEqual(query.getAll("productQuery"),[codes.join(",")]);
  assert.equal([...query].length,6);
  assert.equal(query.get("category"),"净水设备");
  assert.equal(query.get("platform"),"京东");
  assert.equal(forwarded.marker,"preserved");
  assert.equal(forwarded.role,"admin");
  params.set("productCodes","Z");
  const invalid = await route.GET(new Request("http://example.test/api/sales/summary?"+params));
  assert.equal(invalid.status,400);
});

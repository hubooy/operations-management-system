import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";

type Harness = {
  environment: Record<string, string>;
  responses: Array<{ status: number; data: Record<string, unknown>; replayed: boolean }>;
  attestationFails: boolean;
  attestCalls: Array<{ month: string; batchId: string; bytes: Uint8Array }>;
};

const harness: Harness = {
  environment: { TERUISI_FINANCE_RAW_WORKBOOK_BYTES_V2_ENABLED: "true" },
  responses: [], attestationFails: false, attestCalls: [],
};
(globalThis as typeof globalThis & { __financeRawRoute?: Harness }).__financeRawRoute = harness;

function moduleUrl(source: string) {
  return `data:text/javascript,${encodeURIComponent(source)}`;
}

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "cloudflare:workers") return {
      url: moduleUrl("export const env=globalThis.__financeRawRoute.environment;"),
      shortCircuit: true,
    };
    if (specifier === "@/lib/auth/authorization") return {
      url: moduleUrl(`
        export async function requireAppPrincipal(){return {email:'admin@example.test',displayName:'admin',role:'admin',scope:null}}
        export function requireUnrestrictedDataScope(){}
        export function authorizationErrorResponse(){return null}
      `), shortCircuit: true,
    };
    if (specifier === "@/lib/finance/normalized-import") return {
      url: moduleUrl(`
        export async function prepareNormalizedFinanceImport(input){return {
          schemaVersion:'finance-normalized-v1',disposition:'prepared',
          fileName:input.fileName,fileSizeBytes:input.fileSizeBytes,
          rawFileHash:'a'.repeat(64),warnings:[],sourceSheetCount:1,
          months:[{month:'2026-01'}]}}
      `), shortCircuit: true,
    };
    if (specifier === "@/lib/django/finance-service") return {
      url: moduleUrl(`
        export const FINANCE_IMPORTS_PATH='/api/finance/imports';
        export function createDjangoFinanceService(){return {
          request:async()=>globalThis.__financeRawRoute.responses.shift(),
          attestRawWorkbook:async(_principal,input)=>{
            globalThis.__financeRawRoute.attestCalls.push(input);
            if(globalThis.__financeRawRoute.attestationFails) throw Error('attestation unavailable');
            return {status:201,data:{backendRawBytesObserved:true}}
          }
        }}
      `), shortCircuit: true,
    };
    if (specifier === "@/lib/http/api-error") return {
      url: moduleUrl(`
        export function parsePositiveIntegerQuery(){}
        export function safeApiErrorResponse(error){throw error}
      `), shortCircuit: true,
    };
    return nextResolve(specifier, context);
  },
});

const { POST } = await import("../app/api/imports/finance/route.ts");
const bytes = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 1, 2]);
const batch = { id: "a".repeat(64), status: "completed",
  fileName: "synthetic.xlsx", fileSizeBytes: bytes.length,
  months: ["2026-01"] };

function upload() {
  return new Request("http://localhost/api/imports/finance", {
    method: "POST",
    headers: { "content-type": "application/octet-stream",
      "x-file-name": "synthetic.xlsx" },
    body: new Uint8Array(bytes).buffer,
  });
}

test("failed byte follow-up recovers on v1 duplicate without changing its body/status", async () => {
  const firstData = { ok: true, status: "imported", batch };
  const duplicateData = { ok: true, status: "duplicate", batch };
  harness.responses = [
    { status: 201, data: firstData, replayed: false },
    { status: 200, data: duplicateData, replayed: false },
  ];
  harness.attestCalls = [];
  harness.attestationFails = true;
  const first = await POST(upload());
  assert.equal(first.status, 201);
  assert.deepEqual(await first.json(), firstData);
  assert.equal(first.headers.get("x-finance-raw-workbook-attestation"), "unknown");
  assert.equal(harness.attestCalls.length, 1);
  harness.attestationFails = false;
  const replay = await POST(upload());
  assert.equal(replay.status, 200);
  assert.deepEqual(await replay.json(), duplicateData);
  assert.equal(replay.headers.get("x-finance-raw-workbook-attestation"), "verified");
  assert.equal(harness.attestCalls.length, 2);
  assert.equal(harness.attestCalls[1].batchId, batch.id);
  assert.equal(harness.attestCalls[1].month, "2026-01");
  assert.deepEqual(harness.attestCalls[1].bytes, bytes);
  harness.responses = [{ status: 201, data: firstData, replayed: true }];
  const sameRequestReplay = await POST(upload());
  assert.equal(sameRequestReplay.status, 201);
  assert.deepEqual(await sameRequestReplay.json(), firstData);
  assert.equal(sameRequestReplay.headers.get("x-teruisi-write-replay"), "1");
  assert.equal(harness.attestCalls.length, 3);
});

test("wrong month or bytes metadata never dispatches raw attestation", async () => {
  harness.responses = [{ status: 200, data: { ok: true,
    status: "duplicate", batch: { ...batch, id: "b".repeat(64),
      months: ["2026-02"] } }, replayed: false }];
  harness.attestCalls = [];
  const response = await POST(upload());
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("x-finance-raw-workbook-attestation"), "unknown");
  assert.equal(harness.attestCalls.length, 0);
});

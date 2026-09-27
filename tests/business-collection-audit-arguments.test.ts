import assert from "node:assert/strict";
import test from "node:test";
import { executeToolCallWithRegistry, type AiToolEntry } from "../lib/ai/tool-registry-contract";

const principal = { email: "audit@example.test", displayName: "Audit", role: "admin" as const, scope: null };
const entry: AiToolEntry = {
  name: "get_business_netshop_continuation_page", title: "Test continuation",
  description: "Exact internal continuation arguments only.",
  inputSchema: { type: "object", properties: { cursor: { type: "string", maxLength: 1600 } },
    required: ["cursor"], additionalProperties: false },
  annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  risk: "read_only", allowedRoles: ["admin"], scopePolicy: "unscoped_only",
  execution: { environment: "worker_inline", mode: "direct", allowedSurfaces: ["business_collection", "ai_agent"],
    timeoutMs: 1000, maxResultCharacters: 4000, maxCallsPerRequest: 4 },
  handler: async () => ({ returned: 1 }),
};

test("business_collection audit receives exact validated long cursor while other surfaces retain summary", async () => {
  const cursor = "signed-cursor-" + "x".repeat(1000);
  for (const surface of ["business_collection", "ai_agent"] as const) {
    const audits: Array<{ status: string; arguments: unknown }> = [];
    const result = await executeToolCallWithRegistry(entry.name, { cursor }, {
      principal, surface, requestId: `audit-${surface}`,
    }, { entries: [entry], summarizeArguments: value => {
        const supplied = value as { cursor: string };
        return { cursor: supplied.cursor.length > 240 ? `${supplied.cursor.slice(0, 240)}…` : supplied.cursor };
      },
      audit: async value => { audits.push({ status: value.status, arguments: value.arguments }); } });
    assert.equal(result.ok, true);
    const succeeded = audits.find(item => item.status === "succeeded");
    assert.ok(succeeded);
    const stored = succeeded.arguments as { cursor: string };
    if (surface === "business_collection") assert.equal(stored.cursor, cursor);
    else {
      assert.notEqual(stored.cursor, cursor);
      assert.equal(stored.cursor.length, 241);
    }
  }
});

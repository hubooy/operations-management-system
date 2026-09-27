import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const workerUrl = new URL("../worker/index.ts", import.meta.url);

test("scheduled and protected local ticks run one isolated workflow and one formal Agent microstep", async () => {
  const source = await readFile(workerUrl, "utf8");

  assert.match(source, /import \{ wakeAiQueue \} from "\.\.\/lib\/django\/ai-service"/);
  assert.doesNotMatch(source, /runNextAiAgentMicrostep|runNextFormalAiAgentMicrostep|runNextAiWorkflowMicrostep/);
  const maintenanceStart = source.indexOf("async function runScheduledMarketMaintenance");
  const maintenanceEnd = source.indexOf("function allowsLoopbackDevelopmentRequest", maintenanceStart);
  const maintenance = source.slice(maintenanceStart, maintenanceEnd);
  assert.ok(maintenanceStart >= 0 && maintenanceEnd > maintenanceStart);
  for (const queue of ["evidence", "workflow", "agent", "space", "files"]) {
    assert.equal(maintenance.split(`wakeAiQueue("${queue}")`).length - 1, 1);
  }

  const workflowAt = maintenance.indexOf("const aiWorkflow = await runScheduledMarketTask");
  const agentAt = maintenance.indexOf("const aiAgent = await runScheduledMarketTask");
  const netshopProjectionAt = maintenance.indexOf("const netshopProjection = await runScheduledMarketTask");
  const imageCacheAt = maintenance.indexOf("const imageCache = await runScheduledMarketTask");
  const aiSpaceAt = maintenance.indexOf("const aiSpace = await runScheduledMarketTask");
  const annotationsAt = maintenance.indexOf("const annotations = await runScheduledMarketTask");
  assert.ok(workflowAt >= 0 && workflowAt < agentAt && agentAt < netshopProjectionAt,
    "workflow and Agent queues must not starve behind market projection work");
  assert.ok(netshopProjectionAt < imageCacheAt, "the PostgreSQL market projection refresh must precede derived image work");
  assert.ok(imageCacheAt < aiSpaceAt && aiSpaceAt < annotationsAt, "existing runner order must remain stable");
  assert.match(maintenance, /const aiFilesPending = runScheduledMarketTask\(/);
  assert.match(maintenance, /return \{ aiFiles: await aiFilesPending, aiEvidence, aiWorkflow, aiAgent, netshopProjection, imageCache, annotations, aiSpace \};/);

  const localScheduled = source.slice(
    source.indexOf("if (url.pathname === localScheduledPath)"),
    source.indexOf('if (url.pathname === "/_vinext/image")'),
  );
  assert.match(localScheduled, /runScheduledMarketMaintenance\(\{/);

  const scheduled = source.slice(source.indexOf("async scheduled("));
  assert.match(scheduled, /runScheduledMarketMaintenance\(\{/);
});

test("each scheduled runner remains failure-isolated", async () => {
  const source = await readFile(workerUrl, "utf8");
  const maintenance = source.slice(
    source.indexOf("async function runScheduledMarketMaintenance"),
    source.indexOf("function allowsLoopbackDevelopmentRequest"),
  );

  for (const binding of ["aiEvidence", "aiWorkflow", "aiAgent", "netshopProjection", "imageCache", "aiSpace", "annotations"]) {
    assert.match(
      maintenance,
      new RegExp(`const ${binding} = await runScheduledMarketTask\\(`),
      `${binding} must have its own failure boundary`,
    );
  }
});

import { readFile, writeFile } from "node:fs/promises";

import {
  attachHourlyRetryTarget,
  buildHourlyRetryErrorWorkflow,
  hourlyRetryErrorWorkflowId,
  hourlyRetryTargets,
} from "./n8n-hourly-retry-policy.mjs";

const workflowDirectory = new URL("../automation/n8n/", import.meta.url);

for (const fileName of hourlyRetryTargets.map((target) => target.fileName)) {
  const file = new URL(fileName, workflowDirectory);
  const workflow = JSON.parse(await readFile(file, "utf8"));
  attachHourlyRetryTarget(workflow);
  await writeFile(file, `${JSON.stringify(workflow, null, 2)}\n`, "utf8");
}

const errorWorkflowFile = new URL("data-import-hourly-safe-retry.workflow.json", workflowDirectory);
await writeFile(errorWorkflowFile, `${JSON.stringify(buildHourlyRetryErrorWorkflow(), null, 2)}\n`, "utf8");

console.log(JSON.stringify({
  ok: true,
  errorWorkflowId: hourlyRetryErrorWorkflowId,
  targetCount: hourlyRetryTargets.length,
}, null, 2));

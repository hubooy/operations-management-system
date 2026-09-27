import assert from "node:assert/strict";
import test from "node:test";
import { businessVolumeManifest, type BusinessFileRun } from "../lib/ai/business-file-download";

test("renderer 11 temporary proof cannot enter the existing public volume client", () => {
  const digest = "a".repeat(64);
  const item = {
    id: "synthetic-v11", reportId: "synthetic-report", draft: false,
    rendererVersion: 11, status: "ready", version: 2, attempt: 1,
    bindingDigest: digest, storedBytes: 1, errorCode: "",
    progress: { stage: "ready" },
    manifest: { schemaVersion: "business-file-delivery-v2",
      rendererVersion: 11, bindingDigest: digest, attempt: 1, draft: false,
      volumeCount: 1, files: [], manifestFile: {} },
  } as unknown as BusinessFileRun;
  assert.throws(() => businessVolumeManifest(item));
});

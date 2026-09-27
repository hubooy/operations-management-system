// Real Node children exercise the deployed supervisor restart loops. HTTP child
// fixtures isolate lifecycle behavior; the separate PostgreSQL mirror exercises
// the built Worker and all 23 Django services against restored data and ACLs.
import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { once } from "node:events";
import { mkdir, readFile, rm, writeFile, copyFile } from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { tmpdir } from "node:os";
import test from "node:test";
import { fixture, writeCanonical, approvedRecord, installCandidateEntrypoints } from "./fixtures/worker-release-rotation";
import { resolveEffectiveReleaseChain, publishSuccessorRecord } from "../tools/worker-local-release-rotation.mjs";
import { hashTree, sha256Bytes, windowsPathSha256, withPayloadSha256 } from "../tools/worker-local-release.mjs";

async function freePort() {
  const server = net.createServer();
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const port = (server.address() as net.AddressInfo).port;
  await new Promise<void>(resolve => server.close(() => resolve()));
  assert.ok(port > 20000);
  return port;
}

for (const kind of ["worker", "helper"] as const) test(`real ${kind} child restarts without D1 and proof tampering fences the next restart`, { skip: process.platform !== "win32", timeout: 60000 }, async () => {
  const item = await fixture();
  let driver: ReturnType<typeof spawn> | undefined;
  let currentChild: { pid: number; parent: number; creation: string; command: string } | undefined;
  const identity = (pid: number) => JSON.parse(execFileSync("powershell", ["-NoProfile", "-Command",
    `$p=Get-CimInstance Win32_Process -Filter 'ProcessId = ${pid}'; if($p){ [ordered]@{pid=[int]$p.ProcessId;parent=[int]$p.ParentProcessId;creation=$p.CreationDate.ToUniversalTime().ToString('o');command=$p.CommandLine}|ConvertTo-Json -Compress }`], { encoding: "utf8", windowsHide: true }));
  const killExactChild = () => {
    if (!currentChild) return;
    const observed = identity(currentChild.pid);
    assert.deepEqual(observed, currentChild);
    assert.equal(observed.parent, driver!.pid);
    assert.ok(observed.command.includes("mirror-http-child.mjs"));
    process.kill(observed.pid);
    currentChild = undefined;
  };
  try {
    const port = await freePort();
    const release = item.candidate;
    const manifest = JSON.parse(await readFile(release.manifestPath, "utf8"));
    const childRelative = "helper/mirror-http-child.mjs";
    await mkdir(path.join(release.releaseRoot, "helper"));
    const childRaw = Buffer.from('import http from "node:http"; let live=true,failuresRemaining=0; const server=http.createServer((req,res)=>{if(req.url==="/disable" && req.method==="POST"){live=false;res.end("ok");return}if(req.url==="/transient" && req.method==="POST"){failuresRemaining=1;res.end("ok");return}if(req.url==="/_teruisi/local/health/live"){const healthy=live && failuresRemaining-- <= 0;res.statusCode=healthy?200:503;res.end(JSON.stringify({status:healthy?"live":"unavailable"}));return}res.setHeader("content-type","application/json");res.end(JSON.stringify({pid:process.pid}));}); server.listen(Number(process.argv[2]),"127.0.0.1");\n');
    await writeFile(path.join(release.releaseRoot, childRelative), childRaw);
    if (kind === "worker") {
      const adapterRelative = "node_modules/miniflare/dist/src/index.js";
      await mkdir(path.dirname(path.join(release.releaseRoot, adapterRelative)), { recursive: true });
      await copyFile(path.resolve(adapterRelative), path.join(release.releaseRoot, adapterRelative));
      manifest.processIdentity.wranglerEntrypoint = childRelative;
      manifest.processIdentity.fixedWranglerArguments = [String(port)];
      await writeFile(path.join(release.releaseRoot, ".dev.vars"), "MIRROR_ONLY=true\n");
    } else {
      manifest.processIdentity.helperEntrypoint = childRelative;
      manifest.processIdentity.fixedHelperArguments = [String(port)];
      manifest.build.helperRoot = "helper";
      manifest.build.helperTree = await hashTree(path.join(release.releaseRoot, "helper"));
      manifest.artifacts.keyFiles.push({ relativePath: childRelative, sha256: sha256Bytes(childRaw) });
    }
    delete manifest.manifestPayloadSha256;
    release.manifestSha256 = await writeCanonical(release.manifestPath, withPayloadSha256(manifest, "manifestPayloadSha256"));
    const chain = await resolveEffectiveReleaseChain({ runtimeRoot: item.runtime, allowTestRuntimeRoot: true });
    const record = await approvedRecord(item, chain);
    await installCandidateEntrypoints(item);
    await publishSuccessorRecord(item.runtime, record, chain.bootstrap);
    await rm(path.join(item.runtime, "state.sqlite"));
    const driverPath = path.join(release.releaseRoot, "tools/worker-local-runtime-supervisor.mjs");
    await copyFile(path.resolve("tests/fixtures/worker-supervisor-process-driver.mjs"), driverPath);
    const configPath = path.join(item.runtime, "mirror-driver.json");
    await writeCanonical(configPath, { kind, port, runtime: item.runtime, sourceRoot: path.resolve("."), ...release,
      manifest: JSON.parse(await readFile(release.manifestPath, "utf8")) });
    driver = spawn(process.execPath, [driverPath, configPath], { stdio: ["ignore", "pipe", "pipe", "ipc"], windowsHide: true,
      env: { ...Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^(TERUISI|DJANGO|PG|NODE_OPTIONS|MINIFLARE|CLOUDFLARE)/i.test(key))), NODE_ENV: process.env.NODE_ENV } });
    let output = "";
    driver.stdout!.on("data", chunk => { output += chunk.toString(); });
    driver.stderr!.on("data", chunk => { output += chunk.toString(); });
    const exited = once(driver, "exit");
    const driverIdentity = identity(driver.pid!);
    assert.ok(driverIdentity.command.includes(driverPath));
    await writeCanonical(path.join(item.runtime, "state/worker-process.json"), withPayloadSha256({
      version: "teruisi-local-worker-process-v1", releaseId: release.releaseId, manifestSha256: release.manifestSha256,
      manifestPathSha256: windowsPathSha256(release.manifestPath), supervisorEntrypointPathSha256: windowsPathSha256(driverPath),
      supervisorPid: driver.pid, supervisorCreationDate: driverIdentity.creation,
    }, "receiptPayloadSha256"));
    const ready = async (previousPid = 0) => {
      const deadline = Date.now() + 20000;
      while (Date.now() < deadline) {
        if (driver!.exitCode !== null) throw new Error("Supervisor exited: " + output);
        try {
          const response = await fetch(`http://127.0.0.1:${port}/health`, { signal: AbortSignal.timeout(500) });
          const body = await response.json();
          if (body.pid !== previousPid) {
            currentChild = identity(body.pid);
            assert.equal(currentChild!.parent, driver!.pid);
            return body.pid;
          }
        } catch { /* bounded startup polling */ }
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      throw new Error("Supervisor readiness timeout: " + output);
    };
    const firstPid = await ready();
    let lastPid = firstPid;
    if (kind === "worker") {
      const transient = await fetch(`http://127.0.0.1:${port}/transient`, { method: "POST" });
      assert.equal(transient.status, 200);
      await new Promise(resolve => setTimeout(resolve, 700));
      assert.equal(await ready(), firstPid, "a single failed probe must not restart the worker");
      // The HTTP worker is unhealthy while its outer Node process stays alive.
      // A bounded liveness takeover must terminate only this owned process tree.
      const response = await fetch(`http://127.0.0.1:${port}/disable`, { method: "POST" });
      assert.equal(response.status, 200);
      const recoveredPid = await ready(firstPid);
      assert.notEqual(recoveredPid, firstPid);
      lastPid = recoveredPid;
    }
    killExactChild();
    const secondPid = await ready(lastPid);
    assert.notEqual(secondPid, lastPid);
    const rejection = once(driver, "message");
    await writeFile(path.join(release.releaseRoot, "audit/global-d1-retirement.json"), "{}\n");
    killExactChild();
    const [message] = await rejection;
    assert.equal(message.outcome, "rejected");
    assert.match(message.message, /D1 retirement receipt|digest/);
    const [code] = await exited;
    assert.equal(code, 1);
  } finally {
    killExactChild();
    if (driver && driver.exitCode === null) {
      driver.send("stop");
      await once(driver, "exit");
    }
    assert.equal(path.dirname(item.runtime).toLowerCase(), path.resolve(tmpdir()).toLowerCase());
    assert.match(path.basename(item.runtime), /^teruisi-worker-rotation-/);
    await rm(item.runtime, { recursive: true, force: true });
  }
});

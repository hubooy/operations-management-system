import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

for (const shell of ["powershell.exe", "pwsh.exe"]) {
  test(`cold-start coordination with real competing processes (${shell})`, { skip: process.platform !== "win32", timeout: 100_000 }, () => {
    const result = spawnSync(shell, ["-NoProfile", "-NonInteractive", "-File", "tests/cold-start-coordination.test.ps1", "-ChildShell", shell],
      { encoding: "utf8", timeout: 95_000, windowsHide: true });
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}\n${result.error ?? ""}`);
    assert.match(result.stdout, /PASS:/);
  });
}

test("compiled desktop launcher waits for readiness and preserves failures", { skip: process.platform !== "win32", timeout: 45_000 }, () => {
  const root = mkdtempSync(path.join(tmpdir(), "teruisi-cold-launcher-"));
  try {
    const executable = path.join(root, "Launcher.exe");
    const compiler = path.join(process.env.WINDIR ?? "C:\\Windows", "Microsoft.NET/Framework64/v4.0.30319/csc.exe");
    const build = spawnSync(compiler, ["/nologo", "/target:winexe", "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll",
      "/reference:System.Net.Http.dll", "/reference:System.Runtime.Serialization.dll", `/out:${executable}`, path.resolve("tools/desktop-launcher/Launcher.cs")],
    { encoding: "utf8", timeout: 15_000, windowsHide: true });
    assert.equal(build.status, 0, `${build.stdout}\n${build.stderr}`);
    const result = spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-File", "tests/desktop-launcher.test.ps1", "-Executable", executable],
      { encoding: "utf8", timeout: 30_000, windowsHide: true });
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}\n${result.error ?? ""}`);
    assert.match(result.stdout, /PASS:/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

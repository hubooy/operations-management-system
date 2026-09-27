import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { EventEmitter } from 'node:events';
import { spawn as actualSpawn } from 'node:child_process';
import { patchCommandSource } from '../tools/n8n-command-no-console.mjs';

const original = `
function run(command) {
  return (0, child_process_1.spawn)(command, { cwd: process.cwd(), shell: true, detached: true });
}
function cancel(child) {
  return (0, child_process_1.spawn)('taskkill', ['/pid', child.pid.toString(), '/T', '/F']);
}`;

test('patch preserves commands and is idempotent, rejecting unknown and partial versions', () => {
  const patched = patchCommandSource(original);
  assert.equal(patchCommandSource(patched), patched);
  assert.throws(() => patchCommandSource(original + original));
  assert.throws(() => patchCommandSource(original.replace('detached: true', 'detached: false')));
  assert.throws(() => patchCommandSource(patched.replace(", { windowsHide: true })", ')')));
});

test('Windows command and cancellation stay hidden; POSIX process groups remain enabled', () => {
  for (const platform of ['win32', 'linux']) {
    const calls = [];
    const context = vm.createContext({
      child_process_1: { spawn: (...args) => { calls.push(args); return new EventEmitter(); } },
      process: { platform, cwd: () => 'test-directory' },
    });
    vm.runInContext(patchCommandSource(original), context);
    context.run('exact command');
    context.cancel({ pid: 12345 });
    assert.equal(calls[0][0], 'exact command');
    assert.equal(calls[0][1].windowsHide, true);
    assert.equal(calls[0][1].detached, platform !== 'win32');
    assert.equal(calls[0][1].shell, true);
    assert.equal(calls[1][2].windowsHide, true);
    assert.deepEqual(Array.from(calls[1][1]), ['/pid', '12345', '/T', '/F']);
  }
});

test('real Windows PowerShell retains stdout, stderr and failure code with no console', { skip: process.platform !== 'win32' }, async () => {
  const context = vm.createContext({ child_process_1: { spawn: actualSpawn }, process });
  vm.runInContext(patchCommandSource(original), context);
  const code = `Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class Native { [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); }'; [Console]::Out.WriteLine('console=' + [Native]::GetConsoleWindow().ToInt64()); [Console]::Error.WriteLine('synthetic-error'); exit 37`;
  const encoded = Buffer.from(code, 'utf16le').toString('base64');
  const child = context.run(`powershell.exe -NoProfile -NonInteractive -EncodedCommand ${encoded}`);
  let stdout = '', stderr = '';
  child.stdout.on('data', (chunk) => { stdout += chunk; });
  child.stderr.on('data', (chunk) => { stderr += chunk; });
  const exit = await new Promise((resolve, reject) => { child.on('error', reject); child.on('close', resolve); });
  assert.equal(exit, 37);
  assert.match(stdout, /console=0/);
  assert.match(stderr, /synthetic-error/);
});

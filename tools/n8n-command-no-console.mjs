import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const replacements = [
  ['(0, child_process_1.spawn)(command, { cwd: process.cwd(), shell: true, detached: true })',
    "(0, child_process_1.spawn)(command, { cwd: process.cwd(), shell: true, detached: process.platform !== 'win32', windowsHide: true })"],
  ["(0, child_process_1.spawn)('taskkill', ['/pid', child.pid.toString(), '/T', '/F'])",
    "(0, child_process_1.spawn)('taskkill', ['/pid', child.pid.toString(), '/T', '/F'], { windowsHide: true })"],
];

export function patchCommandSource(source) {
  if (replacements.every(([, next]) => source.split(next).length === 2) &&
      replacements.every(([old]) => !source.includes(old))) return source;
  if (!replacements.every(([old, next]) => source.split(old).length === 2 && !source.includes(next))) {
    throw new Error('Unsupported or partially patched ExecuteCommand source; review required.');
  }
  for (const [old, next] of replacements) source = source.replace(old, next);
  return source;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  const [mode, modulePath, backupRoot] = process.argv.slice(2);
  if (!['--check', '--verify', '--apply'].includes(mode) || !modulePath || (mode === '--apply' && !backupRoot)) {
    throw new Error('Usage: node tools/n8n-command-no-console.mjs --check|--verify|--apply <module> [backup-root]');
  }
  const original = fs.readFileSync(modulePath);
  const text = original.toString('utf8');
  const patched = patchCommandSource(text);
  const sha = (value) => crypto.createHash('sha256').update(value).digest('hex');
  const evidence = { module: path.resolve(modulePath), beforeSha256: sha(original), afterSha256: sha(patched), changed: text !== patched, applied: false };
  if (mode === '--verify' && evidence.changed) throw new Error('n8n command no-console patch is missing; apply it before starting the service.');
  if (mode === '--apply' && evidence.changed) {
    if (process.platform !== 'win32') throw new Error('This local patch operator supports Windows only.');
    const shell = path.join(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe');
    const guard = "$ErrorActionPreference='Stop'; $t=Get-ScheduledTask -TaskName 'TERUISI-n8n-Service'; if ($t.State -eq 'Running') {exit 1}; if (@(Get-NetTCPConnection -State Listen -LocalPort 5678 -ErrorAction SilentlyContinue).Count -gt 0) {exit 1}; exit 0";
    execFileSync(shell, ['-NoProfile', '-NonInteractive', '-Command', guard], { windowsHide: true, stdio: 'pipe' });
    // Preserve byte-exact rollback and reject drift after verifying the service is stopped.
    const directory = path.join(backupRoot, evidence.beforeSha256);
    fs.mkdirSync(directory, { recursive: true });
    fs.writeFileSync(path.join(directory, 'ExecuteCommand.node.js.before'), original, { flag: 'wx' });
    if (!fs.readFileSync(modulePath).equals(original)) throw new Error('Module changed during preparation.');
    fs.writeFileSync(modulePath, patched, 'utf8');
    if (sha(fs.readFileSync(modulePath)) !== evidence.afterSha256) throw new Error('Module readback mismatch.');
    evidence.applied = true;
    fs.writeFileSync(path.join(directory, 'receipt.json'), JSON.stringify(evidence, null, 2), { flag: 'wx' });
  }
  console.log(JSON.stringify(evidence));
}

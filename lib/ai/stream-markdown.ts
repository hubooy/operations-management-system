// Presentation only: leave the received/source message untouched.
export function visibleAiMarkdown(source: string, partial: boolean): string {
  if (!partial) return source;
  const lines = source.split("\n");
  const last = lines[lines.length - 1] ?? "";
  if (/^\s*\|/.test(last) || /^\s*(?:#{1,6}|\*{1,2}|~{1,3}|[-+]|\d+[.)]|>)\s*$/.test(last)) lines.pop();
  let tableStart = -1;
  lines.forEach((line, i) => { if (/^\s*\|/.test(line) && (i === 0 || !/^\s*\|/.test(lines[i - 1]!))) tableStart = i; });
  if (tableStart >= 0 && lines.slice(tableStart).every(line => /^\s*\|/.test(line) || !line.trim()) && !/^\s*\|?\s*:?-{3,}/.test(lines[tableStart + 1] ?? "")) lines.splice(tableStart);
  let visible = lines.join("\n");
  const fences = visible.match(/^\s*(?:`{3}|~{3})[^\n]*/gm) ?? [];
  if (fences.length % 2) return visible + "\n" + fences[fences.length - 1]!.trim().slice(0, 3);
  if ((visible.match(/\*\*/g) ?? []).length % 2) visible += "**";
  if ((visible.match(/`/g) ?? []).length % 2) visible += "`";
  return visible;
}

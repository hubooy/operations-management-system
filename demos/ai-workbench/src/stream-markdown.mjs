// Keep incomplete Markdown control characters out of the reading surface.
// The original answer stays untouched for copying and terminal rendering.
export function visibleMarkdown(source, streaming) {
  if (!streaming) return source;
  let visible = source;
  const lines = visible.split("\n");
  const last = lines.at(-1);
  if (/^\s*\|/.test(last)) lines.pop();
  else if (/^\s*(?:#{1,6}|\*{1,2}|~{1,3}|[-+]|\d+[.)]|>)\s*$/.test(last))
    lines.pop();
  const tableStart = lines.findLastIndex(
    (line, i) =>
      /^\s*\|/.test(line) && (i === 0 || !/^\s*\|/.test(lines[i - 1])),
  );
  if (
    tableStart >= 0 &&
    lines
      .slice(tableStart)
      .every((line) => /^\s*\|/.test(line) || !line.trim()) &&
    !/^\s*\|?\s*:?-{3,}/.test(lines[tableStart + 1] || "")
  )
    lines.splice(tableStart);
  visible = lines.join("\n");
  const fences = visible.match(/^\s*(?:`{3}|~{3})[^\n]*/gm) || [];
  if (fences.length % 2)
    return visible + "\n" + fences.at(-1).trim().slice(0, 3);
  if ((visible.match(/(?<!\\)\*\*/g) || []).length % 2) visible += "**";
  if ((visible.match(/(?<!\\)`/g) || []).length % 2) visible += "`";
  return visible;
}

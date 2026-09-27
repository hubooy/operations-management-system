"use client";

import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { visibleAiMarkdown } from "@/lib/ai/stream-markdown";

export default function AiMarkdown({ content, partial = false }: { content: string; partial?: boolean }) {
  return <div className="ai-workbench-markdown"><Markdown remarkPlugins={[remarkGfm]} skipHtml components={{
    table: ({ children }) => <div className="ai-workbench-table"><table data-column-filter-scope="none">{children}</table></div>,
    a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
    img: ({ alt }) => <span className="ai-workbench-image-note">[图片：{alt || "请查看关联产物"}]</span>,
  }}>{visibleAiMarkdown(content, partial)}</Markdown></div>;
}

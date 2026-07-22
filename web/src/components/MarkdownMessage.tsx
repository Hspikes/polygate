import { useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CopyIcon } from "./icons";

function safeUrl(url: string): string {
  if (url.startsWith("#") || url.startsWith("/")) return url;
  try {
    const parsed = new URL(url);
    return ["http:", "https:", "mailto:"].includes(parsed.protocol) ? url : "";
  } catch {
    return "";
  }
}

function Code({ className, children }: { className?: string; children?: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const value = String(children ?? "").replace(/\n$/, "");
  const language = /language-([^ ]+)/.exec(className ?? "")?.[1];
  if (!className) return <code>{children}</code>;
  return (
    <span className="code-block">
      <span className="code-toolbar">
        <span>{language ?? "code"}</span>
        <button
          type="button"
          onClick={() => {
            void navigator.clipboard.writeText(value).then(() => {
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1400);
            });
          }}
        >
          <CopyIcon /> {copied ? "已复制" : "复制"}
        </button>
      </span>
      <code className={className}>{value}</code>
    </span>
  );
}

export function MarkdownMessage({ content }: { content: string }) {
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={safeUrl}
        components={{
          a: ({ href = "", children }) => {
            const safeHref = safeUrl(href);
            return safeHref ? (
              <a href={safeHref} target={href.startsWith("#") ? undefined : "_blank"} rel="noopener noreferrer">
                {children}
              </a>
            ) : <span>{children}</span>;
          },
          img: ({ alt }) => <span className="blocked-image">[已阻止外部图片：{alt || "无描述"}]</span>,
          code: Code,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

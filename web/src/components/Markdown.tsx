import "katex/dist/katex.min.css";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

/** Normalise \( \) and \[ \] (common in model output) to $ and $$, outside code fences. */
function normaliseMath(content: string): string {
  return content
    .split("```")
    .map((part, index) =>
      index % 2
        ? part
        : part
            .replace(/\\\[/g, "\n$$\n")
            .replace(/\\\]/g, "\n$$\n")
            .replace(/\\\(/g, "$")
            .replace(/\\\)/g, "$"),
    )
    .join("```");
}

export function Markdown({ content }: { content: string }) {
  return (
    <div className="prose-chat">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {normaliseMath(content)}
      </ReactMarkdown>
    </div>
  );
}

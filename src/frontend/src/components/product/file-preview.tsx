/**
 * FilePreview — Code/text preview with syntax highlighting and copy.
 *
 * Wraps the existing `CodeBlock` / `CodeBlockCode` primitives to provide
 * a self-contained file-preview experience with filename header, line
 * numbers, scrollable content, and a copy-to-clipboard button.
 *
 * ```tsx
 * <FilePreview
 *   content={sourceCode}
 *   language="python"
 *   filename="optimizer.py"
 *   maxHeight="24rem"
 * />
 * ```
 */
import { useCallback, useState } from "react";
import { Check, Copy, FileCode2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { CodeBlock, CodeBlockCode } from "@/components/ui/code-block";

/* -------------------------------------------------------------------------- */
/*                                   Types                                    */
/* -------------------------------------------------------------------------- */

export interface FilePreviewProps {
  content: string;
  language?: string;
  filename?: string;
  /** Tailwind max-height value, e.g. `"24rem"` or `"50vh"`. */
  maxHeight?: string;
  className?: string;
}

/* -------------------------------------------------------------------------- */
/*                              Component                                     */
/* -------------------------------------------------------------------------- */

export function FilePreview({
  content,
  language = "text",
  filename,
  maxHeight = "32rem",
  className,
}: FilePreviewProps) {
  const [copied, setCopied] = useState(false);

  const copyToClipboard = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API may be unavailable in insecure contexts
    }
  }, [content]);

  const lineCount = content.split("\n").length;

  return (
    <CodeBlock className={cn("overflow-hidden", className)}>
      {/* Header bar */}
      <div className="flex items-center justify-between border-b border-border px-3 py-1.5">
        <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <FileCode2 className="size-3.5" aria-hidden="true" />
          {filename ?? language}
        </span>
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={copyToClipboard}
          aria-label={copied ? "Copied" : "Copy to clipboard"}
        >
          {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
        </Button>
      </div>

      {/* Scrollable code area with line numbers */}
      <div className="flex overflow-auto" style={{ maxHeight }}>
        {/* Line numbers */}
        <div
          className="sticky left-0 shrink-0 select-none border-r border-border/40 bg-muted/20 px-2 py-4 text-right text-[13px] leading-[1.7142857] text-muted-foreground/50"
          aria-hidden="true"
        >
          {Array.from({ length: lineCount }, (_, i) => (
            <div key={i}>{i + 1}</div>
          ))}
        </div>

        {/* Syntax-highlighted code */}
        <CodeBlockCode code={content} language={language} className="flex-1" />
      </div>
    </CodeBlock>
  );
}

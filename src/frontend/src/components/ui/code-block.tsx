import { cn } from "@/lib/utils";
import React, { useEffect, useState } from "react";
import { useIsDark } from "@/stores/theme-store";

export type CodeBlockProps = {
  children?: React.ReactNode;
  className?: string;
} & React.HTMLProps<HTMLDivElement>;

function CodeBlock({ children, className, ...props }: CodeBlockProps) {
  return (
    <div
      className={cn(
        "not-prose flex w-full flex-col overflow-clip border",
        "border-border bg-card text-card-foreground rounded-xl",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

export type CodeBlockCodeProps = {
  code: string;
  language?: string;
  theme?: string;
  className?: string;
} & React.HTMLProps<HTMLDivElement>;

function CodeBlockCode({ code, language = "tsx", theme, className, ...props }: CodeBlockCodeProps) {
  const isDark = useIsDark();
  const activeTheme = theme ?? (isDark ? "github-dark" : "github-light");
  const [highlightedHtml, setHighlightedHtml] = useState<string | null>(null);

  useEffect(() => {
    async function highlight() {
      if (!code) {
        setHighlightedHtml("<pre><code></code></pre>");
        return;
      }

      const { codeToHtml } = await import("shiki");
      const html = await codeToHtml(code, { lang: language, theme: activeTheme });
      setHighlightedHtml(html);
    }
    highlight();
  }, [code, language, activeTheme]);

  const classNames = cn("w-full overflow-x-auto typo-body-sm [&>pre]:px-4 [&>pre]:py-4", className);

  // SSR fallback: render plain code if not hydrated yet
  return highlightedHtml ? (
    <div className={classNames} dangerouslySetInnerHTML={{ __html: highlightedHtml }} {...props} />
  ) : (
    <div className={classNames} {...props}>
      <pre>
        <code>{code}</code>
      </pre>
    </div>
  );
}

export type CodeBlockGroupProps = React.HTMLAttributes<HTMLDivElement>;

function CodeBlockGroup({ children, className, ...props }: CodeBlockGroupProps) {
  return (
    <div className={cn("flex items-center justify-between", className)} {...props}>
      {children}
    </div>
  );
}

export { CodeBlockGroup, CodeBlockCode, CodeBlock };

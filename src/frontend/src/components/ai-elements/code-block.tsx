import type { BundledLanguage } from "shiki";
import { Check, Copy, FileIcon } from "lucide-react";
import React, { useCallback, useState } from "react";

import { Button } from "@/components/ui/button";
import { CodeBlockCode } from "@/components/ui/code-block";
import { cn } from "@/lib/utils";
import { useIsDark } from "@/stores/theme-store";

export type CodeBlockProps = {
  code?: string;
  language?: BundledLanguage | string;
  showLineNumbers?: boolean;
  children?: React.ReactNode;
  className?: string;
} & React.HTMLAttributes<HTMLDivElement>;

export function CodeBlock({
  code = "",
  language = "text",
  showLineNumbers = false,
  children,
  className,
  ...props
}: CodeBlockProps) {
  return (
    <div
      className={cn(
        "not-prose flex w-full flex-col overflow-hidden rounded-an-tool-border-radius border border-border bg-background text-foreground",
        className,
      )}
      {...props}
    >
      {children}
      <CodeBlockContent code={code} language={language} showLineNumbers={showLineNumbers} />
    </div>
  );
}

export type CodeBlockHeaderProps = React.HTMLAttributes<HTMLDivElement>;

export function CodeBlockHeader({ children, className, ...props }: CodeBlockHeaderProps) {
  return (
    <div
      className={cn(
        "flex min-h-7 items-center justify-between border-b border-border bg-an-tool-background/60 px-2.5",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

export type CodeBlockTitleProps = React.HTMLAttributes<HTMLDivElement>;

export function CodeBlockTitle({ children, className, ...props }: CodeBlockTitleProps) {
  return (
    <div
      className={cn(
        "flex min-w-0 items-center gap-1.5 text-an-tool-color-muted typo-caption",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

export type CodeBlockFilenameProps = React.HTMLAttributes<HTMLSpanElement>;

export function CodeBlockFilename({ children, className, ...props }: CodeBlockFilenameProps) {
  return (
    <span className={cn("truncate font-mono", className)} {...props}>
      {children}
    </span>
  );
}

export type CodeBlockActionsProps = React.HTMLAttributes<HTMLDivElement>;

export function CodeBlockActions({ children, className, ...props }: CodeBlockActionsProps) {
  return (
    <div className={cn("flex items-center gap-1", className)} {...props}>
      {children}
    </div>
  );
}

export type CodeBlockCopyButtonProps = {
  code?: string;
  onCopy?: () => void;
  onError?: (error: Error) => void;
  timeout?: number;
  children?: React.ReactNode;
  className?: string;
} & Omit<React.ComponentProps<typeof Button>, "children" | "onClick">;

export function CodeBlockCopyButton({
  code = "",
  onCopy,
  onError,
  timeout = 2000,
  children,
  className,
  ...props
}: CodeBlockCopyButtonProps) {
  const [copied, setCopied] = useState(false);

  const copyToClipboard = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      onCopy?.();
      window.setTimeout(() => setCopied(false), timeout);
    } catch (error) {
      onError?.(error instanceof Error ? error : new Error("Failed to copy code"));
    }
  }, [code, onCopy, onError, timeout]);

  return (
    <Button
      aria-label={copied ? "Copied code" : "Copy code"}
      className={cn("text-an-tool-color-muted hover:text-an-tool-color", className)}
      onClick={copyToClipboard}
      size="icon-xs"
      type="button"
      variant="ghost"
      {...props}
    >
      {children ?? (copied ? <Check className="size-3" /> : <Copy className="size-3" />)}
    </Button>
  );
}

export type CodeBlockContentProps = {
  code?: string;
  language?: BundledLanguage | string;
  showLineNumbers?: boolean;
  className?: string;
} & React.HTMLAttributes<HTMLDivElement>;

export function CodeBlockContent({
  code = "",
  language = "text",
  showLineNumbers = false,
  className,
  ...props
}: CodeBlockContentProps) {
  const lines = code.split("\n");
  const isDark = useIsDark();
  const theme = isDark ? "github-dark" : "github-light";

  return (
    <div
      className={cn("flex max-h-command-output overflow-auto bg-background", className)}
      {...props}
    >
      {showLineNumbers && (
        <div
          aria-hidden="true"
          className="select-none border-r border-border/40 bg-muted/20 px-2 py-3 text-right font-mono typo-body-xs leading-relaxed text-muted-foreground/50"
        >
          {lines.map((_, index) => (
            <div key={index}>{index + 1}</div>
          ))}
        </div>
      )}
      <CodeBlockCode
        code={code}
        language={language}
        theme={theme}
        className={cn(
          "min-w-0 flex-1 wrap-break-word typo-caption [&_code]:whitespace-pre-wrap [&_code]:wrap-break-word [&>pre]:m-0 [&>pre]:whitespace-pre-wrap [&>pre]:wrap-break-word [&>pre]:bg-transparent! [&>pre]:px-3 [&>pre]:py-3",
          showLineNumbers && "[&>pre]:pl-3",
        )}
      />
    </div>
  );
}

export type CodeBlockLanguageSelectorProps = React.HTMLAttributes<HTMLDivElement>;
export function CodeBlockLanguageSelector(props: CodeBlockLanguageSelectorProps) {
  return <CodeBlockActions {...props} />;
}

export type CodeBlockLanguageSelectorTriggerProps = React.HTMLAttributes<HTMLDivElement>;
export function CodeBlockLanguageSelectorTrigger(props: CodeBlockLanguageSelectorTriggerProps) {
  return <div {...props} />;
}

export type CodeBlockLanguageSelectorValueProps = React.HTMLAttributes<HTMLSpanElement>;
export function CodeBlockLanguageSelectorValue(props: CodeBlockLanguageSelectorValueProps) {
  return <span {...props} />;
}

export type CodeBlockLanguageSelectorContentProps = React.HTMLAttributes<HTMLDivElement>;
export function CodeBlockLanguageSelectorContent(props: CodeBlockLanguageSelectorContentProps) {
  return <div {...props} />;
}

export type CodeBlockLanguageSelectorItemProps = React.HTMLAttributes<HTMLDivElement> & {
  value?: string;
};
export function CodeBlockLanguageSelectorItem({
  value: _value,
  ...props
}: CodeBlockLanguageSelectorItemProps) {
  return <div {...props} />;
}

export type CodeBlockContainerProps = React.HTMLAttributes<HTMLDivElement>;
export function CodeBlockContainer(props: CodeBlockContainerProps) {
  return <div {...props} />;
}

export { FileIcon };

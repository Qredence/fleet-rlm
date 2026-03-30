import { useMemo } from "react";
import type { CSSProperties, ReactNode } from "react";
import {
  Archive,
  Clock,
  Copy,
  Database,
  ExternalLink,
  FileCode,
  FileCog,
  FileJson,
  FileText,
  HardDrive,
  Loader2,
  PanelRight,
  Weight,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { rlmApiConfig } from "@/lib/rlm-api/config";
import { cn } from "@/lib/utils";
import {
  type FsNode,
  useFileContent,
  useVolumesSelectionStore,
} from "@/screens/volumes/use-volumes";

export function VolumesCanvasPanel() {
  const selectedFileNode = useVolumesSelectionStore((state) => state.selectedFileNode);

  if (!selectedFileNode) {
    return (
      <Empty className="h-full rounded-none border-0 bg-transparent">
        <EmptyMedia variant="icon">
          <PanelRight />
        </EmptyMedia>
        <EmptyContent>
          <EmptyTitle>No file selected</EmptyTitle>
          <EmptyDescription>Open a file in Volumes to preview its contents here.</EmptyDescription>
        </EmptyContent>
      </Empty>
    );
  }

  return <VolumeFileDetail file={selectedFileNode} />;
}

function getFileIcon(name: string) {
  if (name.endsWith(".md")) return <FileText className="h-5 w-5 text-chart-2" />;
  if (name.endsWith(".py")) return <FileCode className="h-5 w-5 text-chart-1" />;
  if (name.endsWith(".yaml") || name.endsWith(".yml"))
    return <FileCog className="h-5 w-5 text-chart-4" />;
  if (name.endsWith(".json") || name.endsWith(".jsonl"))
    return <FileJson className="h-5 w-5 text-chart-3" />;
  if (name.endsWith(".tar.gz") || name.endsWith(".zip"))
    return <Archive className="h-5 w-5 text-muted-foreground" />;
  if (name.endsWith(".bin") || name.endsWith(".db"))
    return <Database className="h-5 w-5 text-chart-5" />;
  return <FileText className="h-5 w-5 text-muted-foreground" />;
}

function formatDetailFileSize(bytes?: number): string {
  if (!bytes) return "Unknown";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDetailDate(iso?: string): string {
  if (!iso) return "Unknown";
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function isTextFile(name: string, mime?: string): boolean {
  const textExts = [
    ".md",
    ".yaml",
    ".yml",
    ".json",
    ".jsonl",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".txt",
    ".csv",
    ".xml",
    ".html",
    ".css",
  ];
  return textExts.some((ext) => name.endsWith(ext)) || (mime?.startsWith("text/") ?? false);
}

function isMarkdownFile(name: string, mime?: string): boolean {
  return name.endsWith(".md") || name.endsWith(".markdown") || mime === "text/markdown";
}

function getMimeLabel(name: string, mime?: string): string {
  if (name.endsWith(".md")) return "Markdown";
  if (name.endsWith(".py")) return "Python";
  if (name.endsWith(".yaml") || name.endsWith(".yml")) return "YAML";
  if (name.endsWith(".json")) return "JSON";
  if (name.endsWith(".jsonl")) return "JSON Lines";
  if (name.endsWith(".tar.gz")) return "Gzip Archive";
  if (name.endsWith(".bin")) return "Binary";
  if (name.endsWith(".db")) return "Database";
  if (mime) return mime;
  return "File";
}

const DURABLE_ROOTS = ["/data", "/home/daytona/memory"] as const;

function buildMockFileContentMap(): Record<string, string> {
  const entries = DURABLE_ROOTS.flatMap((root) => [
    [
      `${root}/memory/summaries/release-notes.md`,
      `# Durable Memory Summary

- Captures reusable notes derived from prior runs
- Shared across Daytona child sandboxes through the mounted durable volume
`,
    ],
    [
      `${root}/artifacts/reports/execution-summary.md`,
      `# Execution Summary

This report lives in the mounted durable volume so it remains available after sandbox teardown.
`,
    ],
    [
      `${root}/buffers/active-buffer.txt`,
      `workspace = transient
volume = durable
context = staged per run
`,
    ],
    [
      `${root}/meta/workspaces/default/react-session-default.json`,
      `{
  "session_id": "default",
  "storage_mode": "durable_volume",
  "manifest_root": "meta/workspaces/default"
}`,
    ],
    [
      `${root}/meta/workspaces/default/provenance.json`,
      `{
  "builder_mode": "sdk_owned_runtime",
  "volume_layout": ["memory", "artifacts", "buffers", "meta"]
}`,
    ],
  ]);

  return Object.fromEntries(entries);
}

const MOCK_FILE_CONTENT = buildMockFileContentMap();

const METADATA_LABEL_STYLE = {
  fontFamily: "var(--font-sans)",
  fontSize: "var(--font-text-2xs-size)",
  fontWeight: "var(--font-text-2xs-weight)",
  lineHeight: "var(--font-text-2xs-line-height)",
  letterSpacing: "var(--font-text-2xs-tracking)",
  minWidth: "var(--label-min-width)",
} as const;

const FILE_PREVIEW_SURFACE_STYLE = {
  backgroundColor: "var(--color-surface-secondary)",
} as const;

const TEXT_PREVIEW_STYLE = {
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-text-2xs-size)",
  fontWeight: "var(--font-text-2xs-weight)",
  lineHeight: "var(--font-text-xs-line-height)",
  letterSpacing: "var(--font-text-2xs-tracking)",
} as const;

function MetadataRow({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Clock;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center gap-3 py-2">
      <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <span className="shrink-0 text-muted-foreground" style={METADATA_LABEL_STYLE}>
        {label}
      </span>
      <span className="truncate text-foreground typo-caption">{value}</span>
    </div>
  );
}

interface FileDetailProps {
  file: FsNode;
  className?: string;
}

export function VolumeFileDetail({ file, className }: FileDetailProps) {
  const isMobile = useIsMobile();
  const isText = isTextFile(file.name, file.mime);
  const isMarkdown = isMarkdownFile(file.name, file.mime);
  const mock = rlmApiConfig.mockMode;
  const mockContent = mock ? (MOCK_FILE_CONTENT[file.path] ?? null) : null;
  const {
    content: apiContent,
    isLoading: isContentLoading,
    error: contentError,
  } = useFileContent(isText && !mock ? file.path : null, file.provider ?? "modal");
  const resolvedContent = mockContent ?? (apiContent || null);

  const handleCopyPath = () => {
    navigator.clipboard.writeText(file.path);
    toast.success("Path copied to clipboard");
  };

  const handleCopyContent = () => {
    if (resolvedContent) {
      navigator.clipboard.writeText(resolvedContent);
      toast.success("Content copied");
    }
  };

  return (
    <ScrollArea className={cn("h-full", className)}>
      <div className={cn("mx-auto max-w-200", isMobile ? "p-4" : "p-6")}>
        <div className="mb-4 flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-muted">
            {getFileIcon(file.name)}
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-foreground typo-h4">{file.name}</h3>
            <div className="mt-1 flex items-center gap-2">
              <Badge variant="secondary" className="rounded-full">
                <span className="typo-micro">{getMimeLabel(file.name, file.mime)}</span>
              </Badge>
              {file.skillId ? (
                <Badge variant="secondary" className="rounded-full">
                  <span className="typo-micro">Linked to skill</span>
                </Badge>
              ) : null}
            </div>
          </div>
        </div>

        <div className="mb-4 flex items-center gap-2 rounded-lg border-subtle bg-muted/50 p-2.5">
          <HardDrive className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <code className="min-w-0 flex-1 truncate text-foreground typo-mono">{file.path}</code>
          <Button
            variant="ghost"
            className={cn("h-7 w-7 shrink-0 p-0", isMobile && "touch-target")}
            onClick={handleCopyPath}
            aria-label="Copy path"
          >
            <Copy className="h-3.5 w-3.5 text-muted-foreground" />
          </Button>
        </div>

        <Card className="mb-4 border-border-subtle">
          <CardContent className="px-4 py-1">
            <MetadataRow icon={Weight} label="Size" value={formatDetailFileSize(file.size)} />
            <Separator className="border-border-subtle" />
            <MetadataRow icon={Clock} label="Modified" value={formatDetailDate(file.modifiedAt)} />
            <Separator className="border-border-subtle" />
            <MetadataRow icon={FileText} label="Type" value={getMimeLabel(file.name, file.mime)} />
          </CardContent>
        </Card>

        {isText ? (
          <div>
            <div className="mb-2 flex items-center justify-between">
              <span className="text-muted-foreground typo-label">Content Preview</span>
              {resolvedContent ? (
                <Button
                  variant="ghost"
                  className={cn("h-7 gap-1 px-2", isMobile && "touch-target")}
                  onClick={handleCopyContent}
                >
                  <Copy className="h-3 w-3 text-muted-foreground" />
                  <span className="text-muted-foreground typo-helper">Copy</span>
                </Button>
              ) : null}
            </div>
            <div
              className="overflow-hidden rounded-lg border-subtle"
              style={FILE_PREVIEW_SURFACE_STYLE}
            >
              {!mock && isContentLoading ? (
                <div className="flex flex-col items-center gap-2 p-6 text-center">
                  <Loader2 className="h-5 w-5 animate-spin text-muted-foreground motion-reduce:animate-none" />
                  <p className="text-muted-foreground typo-caption">Loading file content...</p>
                </div>
              ) : null}

              {!mock && contentError && !isContentLoading ? (
                <div className="flex flex-col items-center gap-2 p-4 text-center">
                  <ExternalLink className="h-5 w-5 text-destructive" />
                  <p className="text-muted-foreground typo-caption">Failed to load file content.</p>
                  <p className="text-muted-foreground typo-helper">{contentError.message}</p>
                </div>
              ) : null}

              {resolvedContent && !isContentLoading ? (
                isMarkdown ? (
                  <div className="p-4">
                    <SkillMarkdown content={resolvedContent} />
                  </div>
                ) : (
                  <pre
                    className="wrap-break-word overflow-x-auto whitespace-pre-wrap p-4 text-foreground"
                    style={TEXT_PREVIEW_STYLE}
                  >
                    {resolvedContent}
                  </pre>
                )
              ) : !isContentLoading && !contentError ? (
                <div className="flex flex-col items-center gap-2 p-4 text-center">
                  <ExternalLink className="h-5 w-5 text-muted-foreground" />
                  <p className="text-muted-foreground typo-caption">
                    Content preview unavailable in mock mode.
                  </p>
                  <p className="text-muted-foreground typo-helper">
                    Connect the fleet-rlm backend to view file contents.
                  </p>
                </div>
              ) : null}
            </div>
          </div>
        ) : (
          <Card className="border-border-subtle">
            <CardContent className="flex flex-col items-center gap-3 py-8 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-muted">
                {getFileIcon(file.name)}
              </div>
              <p className="text-foreground typo-label">Binary file</p>
              <p className="max-w-75 text-muted-foreground typo-caption">
                This file cannot be previewed in the browser. Download it or open it with an
                external tool.
              </p>
              <Button
                variant="secondary"
                className={cn("mt-2 gap-2 rounded-button", isMobile && "touch-target")}
              >
                <ExternalLink className="h-4 w-4" />
                <span className="typo-label">Open externally</span>
              </Button>
            </CardContent>
          </Card>
        )}
      </div>
    </ScrollArea>
  );
}

interface SkillMarkdownProps {
  content: string;
}

const BOLD_STYLE = {
  fontWeight: "var(--font-weight-semibold)",
} as CSSProperties;

const RELAXED_LINE_HEIGHT_STYLE = {
  lineHeight: "var(--line-height-relaxed)",
} as CSSProperties;

const MARKDOWN_HEADING_STYLES: Record<number, { className: string }> = {
  1: { className: "mb-4 mt-6 text-foreground typo-h2" },
  2: { className: "mb-3 mt-5 text-foreground typo-h3" },
  3: { className: "mb-2 mt-4 text-foreground typo-h4 font-semibold" },
};

function safeHref(href: string): string | undefined {
  try {
    const parsed = new URL(href);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return parsed.toString();
    }
    return undefined;
  } catch {
    return undefined;
  }
}

function parseInline(text: string): ReactNode[] {
  const result: ReactNode[] = [];
  const inlineRegex =
    /(`[^`]+`)|(\*\*[^*]+\*\*)|(__[^_]+__)|(\*[^*]+\*)|(_[^_]+_)|(\[([^\]]+)\]\(([^)]+)\))/g;

  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = inlineRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      result.push(text.slice(lastIndex, match.index));
    }

    if (match[1]) {
      const code = match[1].slice(1, -1);
      result.push(
        <code
          key={match.index}
          className="rounded bg-muted px-1.5 py-0.5 text-foreground typo-mono"
        >
          {code}
        </code>,
      );
    } else if (match[2]) {
      result.push(
        <strong key={match.index} className="text-foreground" style={BOLD_STYLE}>
          {match[2].slice(2, -2)}
        </strong>,
      );
    } else if (match[3]) {
      result.push(
        <strong key={match.index} className="text-foreground" style={BOLD_STYLE}>
          {match[3].slice(2, -2)}
        </strong>,
      );
    } else if (match[4]) {
      result.push(<em key={match.index}>{match[4].slice(1, -1)}</em>);
    } else if (match[5]) {
      result.push(<em key={match.index}>{match[5].slice(1, -1)}</em>);
    } else if (match[6]) {
      const href = safeHref(match[8] ?? "");
      if (href) {
        result.push(
          <a
            key={match.index}
            href={href}
            className="cursor-pointer text-accent hover:underline"
            target="_blank"
            rel="noopener noreferrer nofollow"
          >
            {match[7]}
          </a>,
        );
      } else {
        result.push(match[7] ?? "");
      }
    }

    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    result.push(text.slice(lastIndex));
  }

  return result;
}

interface Block {
  type: "heading" | "paragraph" | "code" | "blockquote" | "ul" | "ol" | "hr";
  level?: number;
  content: string;
  items?: string[];
  lang?: string;
}

function parseBlocks(md: string): Block[] {
  const lines = md.split("\n");
  const blocks: Block[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i] ?? "";

    if (line.startsWith("```")) {
      const lang = line.slice(3).trim();
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !(lines[i] ?? "").startsWith("```")) {
        const codeLine = lines[i];
        if (codeLine == null) break;
        codeLines.push(codeLine);
        i++;
      }
      blocks.push({ type: "code", content: codeLines.join("\n"), lang });
      i++;
      continue;
    }

    if (/^(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      blocks.push({ type: "hr", content: "" });
      i++;
      continue;
    }

    const headingMatch = line.match(/^(#{1,3})\s+(.+)$/);
    if (headingMatch) {
      const hashes = headingMatch[1];
      const headingContent = headingMatch[2];
      if (!hashes || !headingContent) {
        i++;
        continue;
      }
      blocks.push({
        type: "heading",
        level: hashes.length,
        content: headingContent,
      });
      i++;
      continue;
    }

    if (line.startsWith("> ")) {
      const quoteLines: string[] = [];
      while (i < lines.length && (lines[i] ?? "").startsWith("> ")) {
        const quoteLine = lines[i];
        if (quoteLine == null) break;
        quoteLines.push(quoteLine.slice(2));
        i++;
      }
      blocks.push({ type: "blockquote", content: quoteLines.join("\n") });
      continue;
    }

    if (/^[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*+]\s+/.test(lines[i] ?? "")) {
        const itemLine = lines[i];
        if (itemLine == null) break;
        items.push(itemLine.replace(/^[-*+]\s+/, ""));
        i++;
      }
      blocks.push({ type: "ul", content: "", items });
      continue;
    }

    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i] ?? "")) {
        const itemLine = lines[i];
        if (itemLine == null) break;
        items.push(itemLine.replace(/^\d+\.\s+/, ""));
        i++;
      }
      blocks.push({ type: "ol", content: "", items });
      continue;
    }

    if (line.trim() === "") {
      i++;
      continue;
    }

    const paraLines: string[] = [];
    while (
      i < lines.length &&
      (lines[i] ?? "").trim() !== "" &&
      !(lines[i] ?? "").startsWith("#") &&
      !(lines[i] ?? "").startsWith("```") &&
      !(lines[i] ?? "").startsWith("> ") &&
      !/^[-*+]\s+/.test(lines[i] ?? "") &&
      !/^\d+\.\s+/.test(lines[i] ?? "") &&
      !/^(-{3,}|\*{3,}|_{3,})\s*$/.test(lines[i] ?? "")
    ) {
      const paraLine = lines[i];
      if (paraLine == null) break;
      paraLines.push(paraLine);
      i++;
    }
    if (paraLines.length > 0) {
      blocks.push({ type: "paragraph", content: paraLines.join(" ") });
    }
  }

  return blocks;
}

function renderBlock(block: Block, index: number): ReactNode {
  switch (block.type) {
    case "heading": {
      const cfg = MARKDOWN_HEADING_STYLES[block.level ?? 1] ?? MARKDOWN_HEADING_STYLES[3]!;
      const Tag = `h${block.level ?? 1}` as "h1" | "h2" | "h3";
      return (
        <Tag key={index} className={cfg.className}>
          {parseInline(block.content)}
        </Tag>
      );
    }
    case "paragraph":
      return (
        <p key={index} className="mb-3 text-muted-foreground typo-label-regular">
          {parseInline(block.content)}
        </p>
      );
    case "code":
      return (
        <pre key={index} className="mb-4 overflow-x-auto">
          <code className="block overflow-x-auto rounded-lg bg-muted p-4 text-foreground typo-mono">
            {block.content}
          </code>
        </pre>
      );
    case "blockquote":
      return (
        <blockquote
          key={index}
          className="my-4 border-l-4 border-accent pl-4 italic text-muted-foreground typo-label-regular"
        >
          {parseInline(block.content)}
        </blockquote>
      );
    case "ul":
      return (
        <ul
          key={index}
          className="mb-3 ml-5 flex list-disc flex-col gap-1.5 text-muted-foreground typo-label-regular"
        >
          {block.items?.map((item, itemIndex) => (
            <li key={itemIndex} style={RELAXED_LINE_HEIGHT_STYLE}>
              {parseInline(item)}
            </li>
          ))}
        </ul>
      );
    case "ol":
      return (
        <ol
          key={index}
          className="mb-3 ml-5 flex list-decimal flex-col gap-1.5 text-muted-foreground typo-label-regular"
        >
          {block.items?.map((item, itemIndex) => (
            <li key={itemIndex} style={RELAXED_LINE_HEIGHT_STYLE}>
              {parseInline(item)}
            </li>
          ))}
        </ol>
      );
    case "hr":
      return <Separator key={index} className="my-4" />;
    default:
      return null;
  }
}

export function SkillMarkdown({ content }: SkillMarkdownProps) {
  const blocks = useMemo(() => parseBlocks(content), [content]);
  return <div>{blocks.map((block, index) => renderBlock(block, index))}</div>;
}

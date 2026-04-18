// file-detail.tsx
import { Clock, Copy, ExternalLink, FileText, HardDrive, Loader2, Weight } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { rlmApiConfig } from "@/lib/rlm-api/config";
import { cn } from "@/lib/utils";
import { type FsNode, useFileContent } from "@/features/volumes/use-volumes";
import {
  formatDetailDate,
  formatDetailFileSize,
  getFileIcon,
  getMimeLabel,
  isMarkdownFile,
  isTextFile,
} from "@/features/volumes/lib/file-utils";
import { SkillMarkdown } from "./skill-markdown";

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
  } = useFileContent(isText && !mock ? file.path : null, file.provider ?? "daytona");
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

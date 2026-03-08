/**
 * FileDetail — sandbox file detail view shown in the BuilderPanel.
 *
 * Displays file metadata (path, size, MIME, last modified) and file
 * content for text-based files. Binary files show a placeholder.
 *
 * In mock mode, falls back to a local MOCK_FILE_CONTENT map for known
 * paths. In API mode, fetches real content via the `useFileContent` hook.
 *
 * All visual properties reference CSS variables from the design system.
 */
import {
  FileText,
  FileCode,
  FileJson,
  FileCog,
  Archive,
  Database,
  HardDrive,
  Clock,
  Weight,
  Copy,
  ExternalLink,
  Loader2,
} from "lucide-react";
import { toast } from "sonner";
import { typo } from "@/lib/config/typo";
import type { FsNode } from "@/lib/data/types";
import { rlmApiConfig } from "@/lib/rlm-api/config";
import { useFileContent } from "@/hooks/useFilesystem";
import { SkillMarkdown } from "@/components/shared/SkillMarkdown";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils/cn";
import { useIsMobile } from "@/hooks/useIsMobile";

// ── File icon resolver ──────────────────────────────────────────────

function getFileIcon(name: string) {
  if (name.endsWith(".md"))
    return <FileText className="w-5 h-5 text-chart-2" />;
  if (name.endsWith(".py"))
    return <FileCode className="w-5 h-5 text-chart-1" />;
  if (name.endsWith(".yaml") || name.endsWith(".yml"))
    return <FileCog className="w-5 h-5 text-chart-4" />;
  if (name.endsWith(".json") || name.endsWith(".jsonl"))
    return <FileJson className="w-5 h-5 text-chart-3" />;
  if (name.endsWith(".tar.gz") || name.endsWith(".zip"))
    return <Archive className="w-5 h-5 text-muted-foreground" />;
  if (name.endsWith(".bin") || name.endsWith(".db"))
    return <Database className="w-5 h-5 text-chart-5" />;
  return <FileText className="w-5 h-5 text-muted-foreground" />;
}

// ── Helpers ─────────────────────────────────────────────────────────

function formatFileSize(bytes?: number): string {
  if (!bytes) return "Unknown";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso?: string): string {
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
  return (
    textExts.some((ext) => name.endsWith(ext)) ||
    (mime?.startsWith("text/") ?? false)
  );
}

function isMarkdownFile(name: string, mime?: string): boolean {
  return (
    name.endsWith(".md") ||
    name.endsWith(".markdown") ||
    mime === "text/markdown"
  );
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

// ── Mock file content ───────────────────────────────────────────────

const MOCK_FILE_CONTENT: Record<string, string> = {
  "/sandbox/config/fleet.yaml": `# Fleet RLM Configuration
# ─────────────────────────────────
fleet:
  name: "hax-fleet"
  version: "0.4.2"
  environment: "development"

api:
  host: "0.0.0.0"
  port: 8000
  prefix: "/api/v1"
  cors_origins:
    - "http://localhost:5173"
    - "https://fleet.qredence.ai"

taxonomy:
  max_depth: 4
  root_domains:
    - analytics
    - development
    - nlp
    - devops

skills:
  storage_backend: "filesystem"
  base_path: "/sandbox/skills"
  validation:
    require_skill_md: true
    require_manifest: true
    min_quality_score: 70

memory:
  backend: "sqlite"
  path: "/sandbox/data/cache/lru-cache.db"
  max_entries: 10000
  ttl_days: 90
`,
  "/sandbox/config/auth.yaml": `# Authentication Configuration
auth:
  provider: "local"
  session_ttl: 86400  # 24 hours
  jwt_algorithm: "HS256"
  require_email_verification: false
`,
  "/sandbox/config/policies/review-policy.yaml": `# Review Policy
review:
  auto_approve_threshold: 90
  require_human_review_below: 80
  max_review_time_hours: 48
  reviewers:
    - role: "admin"
    - role: "lead"
`,
  "/sandbox/config/policies/publish-policy.yaml": `# Publish Policy
publish:
  require_validation: true
  require_review: true
  min_quality_score: 85
  require_documentation: true
  require_tests: false
`,
  "/sandbox/config/taxonomy.json": `{
  "version": "1.0.0",
  "domains": [
    {
      "name": "analytics",
      "categories": ["data-processing", "preprocessing", "visualization"]
    },
    {
      "name": "development",
      "categories": ["quality-assurance", "testing", "integration"]
    },
    {
      "name": "nlp",
      "categories": ["text-processing", "analysis", "knowledge-management"]
    },
    {
      "name": "devops",
      "categories": ["automation", "observability"]
    }
  ]
}`,
};

// ── Metadata row ────────────────────────────────────────────────────

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
      <Icon className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
      <span
        className="text-muted-foreground shrink-0"
        style={{ ...typo.helper, minWidth: "var(--label-min-width)" }}
      >
        {label}
      </span>
      <span className="text-foreground truncate" style={typo.caption}>
        {value}
      </span>
    </div>
  );
}

// ── Component ───────────────────────────────────────────────────────

interface FileDetailProps {
  file: FsNode;
  className?: string;
}

export function FileDetail({ file, className }: FileDetailProps) {
  const isMobile = useIsMobile();
  const isText = isTextFile(file.name, file.mime);
  const isMarkdown = isMarkdownFile(file.name, file.mime);
  const mock = rlmApiConfig.mockMode;

  // In mock mode, use the static MOCK_FILE_CONTENT map for known paths.
  // In API mode, fetch real content via the useFileContent hook.
  const mockContent = mock ? (MOCK_FILE_CONTENT[file.path] ?? null) : null;

  // Hook is always called (Rules of Hooks), but only fetches in API mode
  // for text files (mock mode returns empty string, which we ignore).
  const {
    content: apiContent,
    isLoading: isContentLoading,
    error: contentError,
  } = useFileContent(isText && !mock ? file.path : null);

  // Resolved content: mock map first, then API content, then null
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
      <div className={cn("max-w-[800px] mx-auto", isMobile ? "p-4" : "p-6")}>
        {/* File header */}
        <div className="flex items-start gap-3 mb-4">
          <div className="w-10 h-10 rounded-lg bg-muted flex items-center justify-center shrink-0">
            {getFileIcon(file.name)}
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="text-foreground truncate" style={typo.h4}>
              {file.name}
            </h3>
            <div className="flex items-center gap-2 mt-1">
              <Badge variant="secondary" className="rounded-full">
                <span style={typo.micro}>
                  {getMimeLabel(file.name, file.mime)}
                </span>
              </Badge>
              {file.skillId && (
                <Badge variant="accent" className="rounded-full">
                  <span style={typo.micro}>Linked to skill</span>
                </Badge>
              )}
            </div>
          </div>
        </div>

        {/* Path + actions */}
        <div className="flex items-center gap-2 mb-4 p-2.5 rounded-lg bg-muted/50 border border-border-subtle">
          <HardDrive className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
          <code
            className="text-foreground flex-1 min-w-0 truncate"
            style={typo.mono}
          >
            {file.path}
          </code>
          <Button
            variant="ghost"
            className={cn("h-7 w-7 p-0 shrink-0", isMobile && "touch-target")}
            onClick={handleCopyPath}
            aria-label="Copy path"
          >
            <Copy className="w-3.5 h-3.5 text-muted-foreground" />
          </Button>
        </div>

        {/* Metadata */}
        <Card className="border-border-subtle mb-4">
          <CardContent className="px-4 py-1">
            <MetadataRow
              icon={Weight}
              label="Size"
              value={formatFileSize(file.size)}
            />
            <Separator className="border-border-subtle" />
            <MetadataRow
              icon={Clock}
              label="Modified"
              value={formatDate(file.modifiedAt)}
            />
            <Separator className="border-border-subtle" />
            <MetadataRow
              icon={FileText}
              label="Type"
              value={getMimeLabel(file.name, file.mime)}
            />
          </CardContent>
        </Card>

        {/* Content preview */}
        {isText ? (
          <div>
            <div className="flex items-center justify-between mb-2">
              <span className="text-muted-foreground" style={typo.label}>
                Content Preview
              </span>
              {resolvedContent && (
                <Button
                  variant="ghost"
                  className={cn("h-7 gap-1 px-2", isMobile && "touch-target")}
                  onClick={handleCopyContent}
                >
                  <Copy className="w-3 h-3 text-muted-foreground" />
                  <span className="text-muted-foreground" style={typo.helper}>
                    Copy
                  </span>
                </Button>
              )}
            </div>
            <div
              className="rounded-lg border border-border-subtle overflow-hidden"
              style={{
                backgroundColor: "var(--input-background, var(--muted))",
              }}
            >
              {/* Loading state (API mode only) */}
              {!mock && isContentLoading && (
                <div className="p-6 flex flex-col items-center gap-2 text-center">
                  <Loader2 className="w-5 h-5 text-muted-foreground animate-spin motion-reduce:animate-none" />
                  <p className="text-muted-foreground" style={typo.caption}>
                    Loading file content...
                  </p>
                </div>
              )}

              {/* Error state (API mode only) */}
              {!mock && contentError && !isContentLoading && (
                <div className="p-4 flex flex-col items-center gap-2 text-center">
                  <ExternalLink className="w-5 h-5 text-destructive" />
                  <p className="text-muted-foreground" style={typo.caption}>
                    Failed to load file content.
                  </p>
                  <p className="text-muted-foreground" style={typo.helper}>
                    {contentError.message}
                  </p>
                </div>
              )}

              {/* Content display */}
              {resolvedContent && !isContentLoading ? (
                isMarkdown ? (
                  <div className="p-4">
                    <SkillMarkdown content={resolvedContent} />
                  </div>
                ) : (
                  <pre
                    className="p-4 overflow-x-auto text-foreground whitespace-pre-wrap wrap-break-word"
                    style={{
                      fontFamily: "var(--font-family-mono)",
                      fontSize: "var(--text-helper)",
                      fontWeight: "var(--font-weight-regular)",
                      lineHeight: "var(--line-height-loose)",
                    }}
                  >
                    {resolvedContent}
                  </pre>
                )
              ) : !isContentLoading && !contentError ? (
                /* No content available (mock mode, path not in map) */
                <div className="p-4 flex flex-col items-center gap-2 text-center">
                  <ExternalLink className="w-5 h-5 text-muted-foreground" />
                  <p className="text-muted-foreground" style={typo.caption}>
                    Content preview unavailable in mock mode.
                  </p>
                  <p className="text-muted-foreground" style={typo.helper}>
                    Connect the fleet-rlm backend to view file contents.
                  </p>
                </div>
              ) : null}
            </div>
          </div>
        ) : (
          /* Binary file placeholder */
          <Card className="border-border-subtle">
            <CardContent className="py-8 flex flex-col items-center gap-3 text-center">
              <div className="w-12 h-12 rounded-lg bg-muted flex items-center justify-center">
                {getFileIcon(file.name)}
              </div>
              <p className="text-foreground" style={typo.label}>
                Binary file
              </p>
              <p
                className="text-muted-foreground max-w-[300px]"
                style={typo.caption}
              >
                This file cannot be previewed in the browser. Download it or
                open it with an external tool.
              </p>
              <Button
                variant="secondary"
                className={cn(
                  "gap-2 rounded-button mt-2",
                  isMobile && "touch-target",
                )}
              >
                <ExternalLink className="w-4 h-4" />
                <span style={typo.label}>Open externally</span>
              </Button>
            </CardContent>
          </Card>
        )}
      </div>
    </ScrollArea>
  );
}

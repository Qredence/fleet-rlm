/**
 * ArtifactViewer — Standalone slide-over panel for inspecting artifact content.
 *
 * Opens in a `DetailDrawer`, renders syntax-highlighted content via `FilePreview`,
 * and displays metadata via `PropertyList`.  Header actions expose copy-to-clipboard
 * and blob-download affordances.
 *
 * ```tsx
 * <ArtifactViewer
 *   open={isOpen}
 *   onOpenChange={setIsOpen}
 *   artifact={{ name: "result.json", content: "{}", language: "json" }}
 * />
 * ```
 */
import { useCallback } from "react";
import { Copy, Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DetailDrawer } from "@/components/product/detail-drawer";
import { FilePreview } from "@/components/product/file-preview";
import { PropertyList, PropertyItem } from "@/components/product/property-list";

/* -------------------------------------------------------------------------- */
/*                                   Types                                    */
/* -------------------------------------------------------------------------- */

export interface ArtifactData {
  name: string;
  content: string;
  language?: string;
  metadata?: Record<string, string>;
}

export interface ArtifactViewerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  artifact: ArtifactData | null;
}

/* -------------------------------------------------------------------------- */
/*                              Helpers                                       */
/* -------------------------------------------------------------------------- */

const EXT_LANGUAGE_MAP: Record<string, string> = {
  ts: "typescript",
  tsx: "tsx",
  js: "javascript",
  jsx: "jsx",
  py: "python",
  rb: "ruby",
  rs: "rust",
  go: "go",
  java: "java",
  kt: "kotlin",
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  yml: "yaml",
  yaml: "yaml",
  json: "json",
  md: "markdown",
  css: "css",
  scss: "scss",
  html: "html",
  xml: "xml",
  sql: "sql",
  toml: "toml",
  dockerfile: "dockerfile",
};

function detectLanguage(filename: string): string {
  const ext = filename.split(".").pop()?.toLowerCase() ?? "";
  return EXT_LANGUAGE_MAP[ext] ?? "text";
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/* -------------------------------------------------------------------------- */
/*                              Component                                     */
/* -------------------------------------------------------------------------- */

export function ArtifactViewer({ open, onOpenChange, artifact }: ArtifactViewerProps) {
  const copyToClipboard = useCallback(async () => {
    if (!artifact) return;
    try {
      await navigator.clipboard.writeText(artifact.content);
    } catch {
      // Clipboard API may be unavailable in insecure contexts — silent fallback
    }
  }, [artifact]);

  const downloadArtifact = useCallback(() => {
    if (!artifact) return;
    const blob = new Blob([artifact.content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = artifact.name;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  }, [artifact]);

  if (!artifact) {
    return (
      <DetailDrawer open={open} onOpenChange={onOpenChange} title="Artifact">
        <p className="py-8 text-center text-sm text-muted-foreground">No artifact selected.</p>
      </DetailDrawer>
    );
  }

  const language = artifact.language ?? detectLanguage(artifact.name);
  const sizeDisplay = formatBytes(new TextEncoder().encode(artifact.content).byteLength);

  const headerActions = (
    <>
      <Button
        variant="ghost"
        size="icon-xs"
        onClick={copyToClipboard}
        aria-label="Copy to clipboard"
      >
        <Copy className="size-3.5" />
      </Button>
      <Button
        variant="ghost"
        size="icon-xs"
        onClick={downloadArtifact}
        aria-label="Download artifact"
      >
        <Download className="size-3.5" />
      </Button>
    </>
  );

  return (
    <DetailDrawer
      open={open}
      onOpenChange={onOpenChange}
      title={artifact.name}
      actions={headerActions}
    >
      <div className="flex flex-col gap-6 pb-4">
        <FilePreview content={artifact.content} language={language} filename={artifact.name} />

        <PropertyList>
          <PropertyItem label="Filename" value={artifact.name} />
          <PropertyItem label="Language" value={language} />
          <PropertyItem label="Size" value={sizeDisplay} />
          {artifact.metadata
            ? Object.entries(artifact.metadata).map(([key, value]) => (
                <PropertyItem key={key} label={key} value={value} />
              ))
            : null}
        </PropertyList>
      </div>
    </DetailDrawer>
  );
}

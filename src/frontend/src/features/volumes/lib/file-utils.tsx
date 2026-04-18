import type { ReactNode } from "react";
import { Archive, Database, FileCode, FileCog, FileJson, FileText } from "lucide-react";

export function getFileIcon(name: string): ReactNode {
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

export function formatDetailFileSize(bytes?: number): string {
  if (!bytes) return "Unknown";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDetailDate(iso?: string): string {
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

export function isTextFile(name: string, mime?: string): boolean {
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

export function isMarkdownFile(name: string, mime?: string): boolean {
  return name.endsWith(".md") || name.endsWith(".markdown") || mime === "text/markdown";
}

export function getMimeLabel(name: string, mime?: string): string {
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

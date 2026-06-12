import type { OptimizationRunResponse } from "@/lib/rlm-api";

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function formatScore(score: number | null | undefined): string {
  return typeof score === "number" ? score.toFixed(3) : "-";
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export function targetLabel(run: OptimizationRunResponse): string {
  if (run.module_slug) return run.module_slug;
  if (run.program_spec.startsWith("skill:")) return run.program_spec;
  return run.program_spec;
}

export function shortPath(path: string | null | undefined): string {
  if (!path) return "-";
  const parts = path.split("/");
  return parts.length > 4 ? `.../${parts.slice(-4).join("/")}` : path;
}

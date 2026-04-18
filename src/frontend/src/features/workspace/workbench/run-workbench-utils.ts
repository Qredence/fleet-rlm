export function humanizeKind(kind: string): string {
  return kind
    .replace(/_/g, " ")
    .replace(/\brlm\b/gi, "RLM")
    .replace(/\brepl\b/gi, "REPL");
}

export function statusBadgeVariant(
  status: string,
): "default" | "secondary" | "outline" | "destructive" {
  if (status === "completed") return "default";
  if (status === "needs_human_review") return "outline";
  if (status === "error") return "destructive";
  if (status === "running") return "secondary";
  return "outline";
}

export function stringifyValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function preferredArtifactText(value: unknown): string | null {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || null;
  }

  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }

  const record = value as Record<string, unknown>;
  for (const key of ["final_markdown", "summary", "text", "content", "message"]) {
    const candidate = record[key];
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate;
    }
  }

  const nestedValue = record.value;
  if (nestedValue !== value) {
    return preferredArtifactText(nestedValue);
  }

  return null;
}

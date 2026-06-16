function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isGenericLabel(value: string | undefined) {
  if (!value) return true;
  const normalized = value.trim().toLowerCase();
  return normalized === "" || normalized === "tool" || normalized === "unknown";
}

function humanizePartType(partType: string | undefined) {
  if (!partType) return "Tool";
  const base = partType.startsWith("tool-") ? partType.slice(5) : partType;
  if (isGenericLabel(base)) return "Tool";
  return base
    .replace(/_/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/\b\w/g, (char) => char.toUpperCase())
    .trim();
}

function summarizeObjectFields(record: Record<string, unknown>) {
  const candidates = [
    record.description,
    record.message,
    record.query,
    record.pattern,
    record.url,
    record.path,
    record.file_path,
    record.command,
    record.title,
  ];
  for (const candidate of candidates) {
    if (typeof candidate === "string" && candidate.trim()) return candidate.trim();
  }
  return "";
}

function truncate(value: string, max = 64) {
  return value.length > max ? `${value.slice(0, max - 3)}...` : value;
}

export function deriveFallbackToolPresentation(part: any) {
  const rawToolName =
    typeof part?.toolName === "string" && part.toolName.trim() ? part.toolName.trim() : "";
  const inputSummary = isRecord(part?.input) ? summarizeObjectFields(part.input) : "";
  const outputSummary = isRecord(part?.output) ? summarizeObjectFields(part.output) : "";
  const detail = inputSummary || outputSummary;

  const title = isGenericLabel(rawToolName)
    ? detail
      ? truncate(detail, 28)
      : humanizePartType(part?.type)
    : humanizePartType(rawToolName);

  const subtitle =
    !isGenericLabel(rawToolName) && rawToolName.toLowerCase() !== title.toLowerCase()
      ? rawToolName
      : detail && detail !== title
        ? truncate(detail, 56)
        : undefined;

  return { title, subtitle };
}

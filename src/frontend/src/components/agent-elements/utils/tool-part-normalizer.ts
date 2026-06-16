type AnyRecord = Record<string, any>;

function isRecord(value: unknown): value is AnyRecord {
  return typeof value === "object" && value !== null;
}

function parseStructuredJson(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!trimmed) return value;

  try {
    const parsed = JSON.parse(trimmed);
    return isRecord(parsed) || Array.isArray(parsed) ? parsed : value;
  } catch {
    return value;
  }
}

function isGroupableToolPart(part: unknown): boolean {
  if (!isRecord(part)) return false;
  const type = part.type;
  if (typeof type !== "string" || !type.startsWith("tool-")) return false;

  const nonGroupableTypes = new Set([
    "tool-Bash",
    "tool-Edit",
    "tool-Write",
    "tool-Task",
    "tool-Agent",
    "tool-PlanWrite",
    "tool-TodoWrite",
    "tool-Question",
    "tool-TaskOutput",
  ]);

  if (nonGroupableTypes.has(type)) return false;

  const toolCallId = part.toolCallId;
  if (typeof toolCallId === "string" && toolCallId.includes(":")) {
    return false;
  }

  return true;
}

export function normalizeToolPart(part: unknown): unknown {
  if (!isRecord(part)) return part;
  if (typeof part.type !== "string" || !part.type.startsWith("tool-")) return part;

  const normalizedInput = parseStructuredJson(part.input);
  const normalizedOutput = parseStructuredJson(part.output);
  const normalizedResult = parseStructuredJson(part.result);

  const inputChanged = normalizedInput !== part.input;
  const outputChanged = normalizedOutput !== part.output;
  const resultChanged = normalizedResult !== part.result;

  if (!inputChanged && !outputChanged && !resultChanged) {
    return part;
  }

  const normalizedPart: AnyRecord = { ...part };
  if (inputChanged) normalizedPart.input = normalizedInput;
  if (outputChanged) normalizedPart.output = normalizedOutput;
  if (resultChanged) normalizedPart.result = normalizedResult;
  return normalizedPart;
}

export function normalizeAssistantToolParts(parts: unknown[]): unknown[] {
  let changed = false;
  const normalizedParts = parts.map((part) => {
    const normalizedPart = normalizeToolPart(part);
    if (normalizedPart !== part) changed = true;
    return normalizedPart;
  });

  const groupedParts: unknown[] = [];
  let i = 0;
  while (i < normalizedParts.length) {
    const part = normalizedParts[i]!;
    if (isGroupableToolPart(part)) {
      const group: unknown[] = [part];
      let j = i + 1;
      while (j < normalizedParts.length && isGroupableToolPart(normalizedParts[j]!)) {
        group.push(normalizedParts[j]!);
        j++;
      }
      if (group.length >= 2) {
        changed = true;
        const groupCallId = (part as AnyRecord).toolCallId ?? `group-${i}`;
        groupedParts.push({
          type: "tool-Group",
          toolCallId: groupCallId,
          nestedTools: group,
          state: group.some((p: any) => p.state === "input-streaming" || p.state === "partial-call")
            ? "input-streaming"
            : "output-available",
          startedAt: (part as AnyRecord).startedAt,
          callProviderMetadata: (part as AnyRecord).callProviderMetadata,
        });
        i = j;
      } else {
        groupedParts.push(part);
        i++;
      }
    } else {
      groupedParts.push(part);
      i++;
    }
  }

  return changed ? groupedParts : parts;
}

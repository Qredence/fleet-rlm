type ErrorDetails = {
  message: string;
  code?: string;
  trace?: string;
};

type TrajectoryChain = {
  thought?: string;
  action?: string;
  observation?: string;
};

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return;
  return value as Record<string, unknown>;
}

function asString(value: unknown): string | undefined {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || undefined;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return undefined;
}

function stringify(value: unknown): string {
  if (typeof value === "string") return value;
  if (value == null) return "";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function looksLikeCode(text: string): boolean {
  const value = text.trim();
  if (!value) return false;
  if (value.includes("\n")) return true;
  return /(def |function |class |import |from |const |let |return |for\s*\(|while\s*\(|print\()/.test(
    value,
  );
}

function findCode(value: unknown): string | undefined {
  const direct = asString(value);
  if (direct && looksLikeCode(direct)) return direct;

  const record = asRecord(value);
  if (!record) return;

  const candidateKeys = [
    "code",
    "source",
    "python",
    "script",
    "content",
    "text",
  ];
  for (const key of candidateKeys) {
    const found = asString(record[key]);
    if (found && looksLikeCode(found)) return found;
  }

  const nestedInput = asRecord(record.input);
  if (nestedInput) {
    for (const key of candidateKeys) {
      const found = asString(nestedInput[key]);
      if (found && looksLikeCode(found)) return found;
    }
  }

  return;
}

export function extractReplCodePreview(payload: {
  type: string;
  input?: unknown;
  output?: unknown;
}): string | undefined {
  if (payload.type !== "repl") return;
  return findCode(payload.input) ?? findCode(payload.output);
}

function findErrorRecord(value: unknown): Record<string, unknown> | undefined {
  const record = asRecord(value);
  if (!record) return;
  const nestedError = asRecord(record.error);
  if (nestedError) return nestedError;
  if (
    typeof record.message === "string" ||
    typeof record.traceback === "string" ||
    typeof record.stack === "string"
  ) {
    return record;
  }
  const output = asRecord(record.output);
  if (output) return findErrorRecord(output);
  return;
}

export function extractErrorDetails(payload: {
  label: string;
  status: "streaming" | "complete" | "error";
  input?: unknown;
  output?: unknown;
}): ErrorDetails | undefined {
  if (
    payload.status !== "error" &&
    !/error|failed|exception/i.test(payload.label || "")
  ) {
    return;
  }

  const fromOutput = findErrorRecord(payload.output);
  const fromInput = findErrorRecord(payload.input);
  const source = fromOutput ?? fromInput;
  if (source) {
    const message =
      asString(source.message) ??
      asString(source.error) ??
      asString(source.detail) ??
      "Execution failed";
    const code = asString(source.code) ?? asString(source.type);
    const trace =
      asString(source.traceback) ??
      asString(source.stack) ??
      asString(source.trace);
    return { message, code, trace };
  }

  const raw = stringify(payload.output || payload.input).trim();
  if (!raw) return { message: "Execution failed" };
  const [firstLine, ...rest] = raw.split(/\r?\n/);
  return {
    message: firstLine?.trim() || "Execution failed",
    trace: rest.join("\n").trim() || undefined,
  };
}

function normalizeTrajectorySource(
  value: unknown,
): Record<string, unknown> | undefined {
  const record = asRecord(value);
  if (!record) return;
  const nested = asRecord(record.trajectory_step) ?? asRecord(record.step_data);
  return nested ?? record;
}

function summarizeAction(record: Record<string, unknown>): string | undefined {
  const action =
    asString(record.action) ??
    asString(record.tool_name) ??
    asString(record.tool) ??
    asString(record.label);
  const args =
    asString(record.args) ??
    asString(record.tool_input) ??
    stringify(record.input).trim();
  if (!action) return;
  if (!args || args === "{}") return action;
  return `${action}: ${args.length > 140 ? `${args.slice(0, 140)}…` : args}`;
}

function summarizeObservation(
  record: Record<string, unknown>,
): string | undefined {
  const raw =
    asString(record.observation) ??
    asString(record.result) ??
    asString(record.output) ??
    stringify(record.output).trim();
  if (!raw || raw === "{}") return;
  return raw.length > 220 ? `${raw.slice(0, 220)}…` : raw;
}

export function extractTrajectoryChain(payload: {
  type: string;
  input?: unknown;
  output?: unknown;
}): TrajectoryChain | undefined {
  const source =
    normalizeTrajectorySource(payload.output) ??
    normalizeTrajectorySource(payload.input);
  if (!source) return;

  const thought = asString(source.thought) ?? asString(source.reasoning);
  const action = summarizeAction(source);
  const observation = summarizeObservation(source);

  if (!thought && !action && !observation) return;
  return { thought, action, observation };
}

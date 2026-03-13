import type { RuntimeContext } from "@/lib/data/types";

export function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  return value as Record<string, unknown>;
}

export function asOptionalText(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed ? trimmed : undefined;
}

export function asOptionalNumber(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}

export function parseRuntimeContext(
  payload?: Record<string, unknown>,
): RuntimeContext | undefined {
  const raw = asRecord(payload?.runtime) ?? payload;
  if (!raw) return undefined;
  const depth = asOptionalNumber(raw.depth);
  const maxDepth = asOptionalNumber(raw.max_depth);
  const executionProfile = asOptionalText(raw.execution_profile);
  if (depth == null || maxDepth == null || !executionProfile) return undefined;
  const volumeName = asOptionalText(raw.volume_name);
  const executionMode = asOptionalText(raw.execution_mode);
  const runtimeMode = asOptionalText(raw.runtime_mode);
  const sandboxId = asOptionalText(raw.sandbox_id);
  return {
    depth,
    maxDepth,
    executionProfile,
    sandboxActive: raw.sandbox_active === true,
    effectiveMaxIters: asOptionalNumber(raw.effective_max_iters) ?? 10,
    ...(volumeName ? { volumeName } : {}),
    ...(executionMode ? { executionMode } : {}),
    ...(runtimeMode ? { runtimeMode } : {}),
    ...(sandboxId ? { sandboxId } : {}),
  };
}

export function stringifyUnknown(value: unknown): string | undefined {
  if (value == null) return undefined;
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed ? trimmed : undefined;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

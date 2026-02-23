import { z } from "zod";

const UnknownRecordSchema = z.record(z.string(), z.unknown());

export const TrajectoryStepSchema = z.object({
  id: z.union([z.string(), z.number()]).optional(),
  thought: z.string().optional(),
  tool_name: z.string().optional(),
  label: z.string().optional(),
  input: z.unknown().optional(),
  output: z.unknown().optional(),
});

export const TrajectoryEnvelopeSchema = z.union([
  z.object({
    trajectory_step: TrajectoryStepSchema,
    text: z.string().optional(),
  }),
  z.object({
    step_data: TrajectoryStepSchema,
    text: z.string().optional(),
  }),
  TrajectoryStepSchema,
]);

export const ToolPayloadSchema = z.object({
  tool_name: z.string().optional(),
  tool_input: z.unknown().optional(),
  tool_args: z.unknown().optional(),
  tool_output: z.unknown().optional(),
  result: z.unknown().optional(),
  error: z.unknown().optional(),
});

export const ErrorPayloadSchema = z.union([
  z.object({
    error: z.object({
      message: z.string().optional(),
      code: z.string().optional(),
      traceback: z.string().optional(),
      stack: z.string().optional(),
      type: z.string().optional(),
    }),
  }),
  z.object({
    message: z.string().optional(),
    code: z.string().optional(),
    traceback: z.string().optional(),
    stack: z.string().optional(),
    type: z.string().optional(),
  }),
]);

export const FinalOutputPayloadEnvelopeSchema = z
  .object({
    final_reasoning: z.unknown().optional(),
    guardrail_warnings: z.array(z.unknown()).optional(),
  })
  .catchall(z.unknown());

function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return;
  return value as Record<string, unknown>;
}

export type ParsedArtifactPayload =
  | { kind: "trajectory"; data: z.infer<typeof TrajectoryEnvelopeSchema> }
  | { kind: "tool"; data: z.infer<typeof ToolPayloadSchema> }
  | { kind: "error"; data: z.infer<typeof ErrorPayloadSchema> }
  | { kind: "record"; data: Record<string, unknown> }
  | { kind: "unknown"; data: unknown };

export function parseArtifactPayload(value: unknown): ParsedArtifactPayload {
  const record = asRecord(value);
  const looksLikeError =
    !!record &&
    ("error" in record ||
      "traceback" in record ||
      "stack" in record ||
      ("message" in record && typeof record.message === "string"));
  if (looksLikeError) {
    const error = ErrorPayloadSchema.safeParse(value);
    if (error.success) return { kind: "error", data: error.data };
  }

  const looksLikeTool =
    !!record &&
    ("tool_name" in record ||
      "tool_input" in record ||
      "tool_args" in record ||
      "tool_output" in record);
  if (looksLikeTool) {
    const tool = ToolPayloadSchema.safeParse(value);
    if (tool.success) return { kind: "tool", data: tool.data };
  }

  const looksLikeTrajectory =
    !!record &&
    ("trajectory_step" in record ||
      "step_data" in record ||
      "thought" in record ||
      ("tool_name" in record &&
        "output" in record &&
        !("tool_output" in record)));
  if (looksLikeTrajectory) {
    const trajectory = TrajectoryEnvelopeSchema.safeParse(value);
    if (trajectory.success)
      return { kind: "trajectory", data: trajectory.data };
  }

  if (record) {
    const anyRecord = UnknownRecordSchema.safeParse(record);
    if (anyRecord.success) return { kind: "record", data: record };
  }

  return { kind: "unknown", data: value };
}

export function parseFinalOutputEnvelope(
  value: unknown,
): z.infer<typeof FinalOutputPayloadEnvelopeSchema> | undefined {
  const result = FinalOutputPayloadEnvelopeSchema.safeParse(value);
  return result.success ? result.data : undefined;
}

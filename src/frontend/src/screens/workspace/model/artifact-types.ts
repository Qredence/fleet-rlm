export type ArtifactStepType = "llm" | "repl" | "tool" | "memory" | "output";

export type ArtifactActorKind = "root_rlm" | "sub_agent" | "delegate" | "unknown";

export interface ExecutionStep {
  id: string;
  parent_id?: string;
  sequence?: number;
  type: ArtifactStepType;
  label: string;
  depth?: number | null;
  actor_kind?: ArtifactActorKind | null;
  actor_id?: string | null;
  lane_key?: string | null;
  input?: unknown;
  output?: unknown;
  timestamp: number;
}

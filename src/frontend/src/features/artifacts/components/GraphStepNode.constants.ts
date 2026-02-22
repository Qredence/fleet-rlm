import {
  Brain,
  Database,
  FileOutput,
  Terminal,
  Wrench,
} from "lucide-react";

import type { ArtifactStepType } from "@/stores/artifactStore";

const STEP_TYPE_META: Record<
  ArtifactStepType,
  { color: string; label: string; Icon: typeof Brain }
> = {
  llm: { color: "var(--chart-3)", label: "LLM", Icon: Brain },
  repl: { color: "var(--chart-4)", label: "REPL", Icon: Terminal },
  tool: { color: "var(--chart-2)", label: "Tool", Icon: Wrench },
  memory: { color: "var(--chart-1)", label: "Memory", Icon: Database },
  output: { color: "var(--accent)", label: "Output", Icon: FileOutput },
};

const NODE_WIDTH = 220;

export { NODE_WIDTH, STEP_TYPE_META };

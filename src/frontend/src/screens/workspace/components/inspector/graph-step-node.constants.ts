import { Brain, Database, FileOutput, Terminal, Wrench } from "lucide-react";

import type { ArtifactStepType } from "@/screens/workspace/model/artifact-types";

const STEP_TYPE_META: Record<
  ArtifactStepType,
  { color: string; label: string; Icon: typeof Brain }
> = {
  llm: { color: "var(--trace-llm)", label: "LLM", Icon: Brain },
  repl: { color: "var(--trace-repl)", label: "REPL", Icon: Terminal },
  tool: { color: "var(--trace-tool)", label: "Tool", Icon: Wrench },
  memory: { color: "var(--trace-memory)", label: "Memory", Icon: Database },
  output: { color: "var(--trace-output)", label: "Output", Icon: FileOutput },
};

const NODE_WIDTH = 296;

export { NODE_WIDTH, STEP_TYPE_META };

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";

import { ArtifactGraph } from "@/screens/workspace/components/inspector/artifact-graph";
import type { ExecutionStep } from "@/screens/workspace/model/artifact-types";

const steps: ExecutionStep[] = [
  {
    id: "step-1",
    type: "llm",
    label: "Plan",
    timestamp: 1,
    actor_kind: "root_rlm",
    lane_key: "root_rlm",
  },
];

describe("ArtifactGraph visibility gating", () => {
  it("does not render the React Flow surface while hidden", () => {
    const html = renderToStaticMarkup(
      <ArtifactGraph
        steps={steps}
        activeStepId="step-1"
        onSelectStep={() => {}}
        isVisible={false}
      />,
    );

    expect(html).not.toContain("artifact-graph-flow");
    expect(html).not.toContain("Preparing graph");
  });
});

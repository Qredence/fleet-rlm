import { memo } from "react";
import type { ExecutionStep } from "@/features/workspace/use-workspace";
import { InspectorTabPanel } from "../inspector-tab-panel";
import { GraphInspectorContent } from "./graph-inspector-content";

export const GraphInspectorTab = memo(function GraphInspectorTab({
  steps,
}: {
  steps: ExecutionStep[];
}) {
  return (
    <InspectorTabPanel value="graph">
      <GraphInspectorContent steps={steps} />
    </InspectorTabPanel>
  );
});

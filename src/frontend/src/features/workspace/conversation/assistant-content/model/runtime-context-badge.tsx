import { inspectorStyles } from "@/features/workspace/inspection/inspector-styles";
import type { RuntimeContext } from "@/lib/workspace/workspace-types";
import { getRuntimeBadgeStrings } from "./runtime-badges";

export function RuntimeContextBadge({ ctx }: { ctx?: RuntimeContext }) {
  const pills = getRuntimeBadgeStrings(ctx);
  if (pills.length === 0) return null;

  return <div className={inspectorStyles.runtime.inline}>{pills.join(" · ")}</div>;
}

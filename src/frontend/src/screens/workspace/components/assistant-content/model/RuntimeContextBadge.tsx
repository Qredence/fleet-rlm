import { inspectorStyles } from "@/screens/workspace/components/inspector/inspector-styles";
import type { RuntimeContext } from "@/screens/workspace/model/workspace-types";
import { getRuntimeBadgeStrings } from "./runtimeBadges";

export function RuntimeContextBadge({ ctx }: { ctx?: RuntimeContext }) {
  const pills = getRuntimeBadgeStrings(ctx);
  if (pills.length === 0) return null;

  return <div className={inspectorStyles.runtime.inline}>{pills.join(" · ")}</div>;
}

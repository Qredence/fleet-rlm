import { inspectorStyles } from "@/features/rlm-workspace/shared/inspector-styles";
import type { RuntimeContext } from "@/lib/data/types";
import { getRuntimeBadgeStrings } from "./runtimeBadges";

export function RuntimeContextBadge({ ctx }: { ctx?: RuntimeContext }) {
  const pills = getRuntimeBadgeStrings(ctx);
  if (pills.length === 0) return null;

  return <div className={inspectorStyles.runtime.inline}>{pills.join(" · ")}</div>;
}

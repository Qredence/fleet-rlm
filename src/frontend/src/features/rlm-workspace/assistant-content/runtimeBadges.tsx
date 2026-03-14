import { inspectorStyles } from "@/features/rlm-workspace/shared/inspector-styles";
import type { RuntimeContext } from "@/lib/data/types";

export function getRuntimeBadgeStrings(ctx?: RuntimeContext): string[] {
  if (!ctx) return [];

  const pills: string[] = [];
  if (ctx.depth > 0) pills.push(`depth ${ctx.depth}/${ctx.maxDepth}`);
  if (ctx.runtimeMode && ctx.runtimeMode !== "modal_chat") {
    pills.push(`runtime ${ctx.runtimeMode}`);
  }
  if (ctx.executionMode && ctx.executionMode !== "react") {
    pills.push(`mode ${ctx.executionMode}`);
  }
  if (ctx.sandboxActive) pills.push("sandbox");
  if (ctx.sandboxId) pills.push(`sandbox ${ctx.sandboxId.slice(0, 10)}`);
  if (ctx.volumeName) pills.push(ctx.volumeName);
  if (ctx.executionProfile !== "ROOT_INTERLOCUTOR") {
    pills.push(ctx.executionProfile.toLowerCase().replace(/_/g, " "));
  }
  return pills;
}

export function RuntimeContextBadge({ ctx }: { ctx?: RuntimeContext }) {
  const pills = getRuntimeBadgeStrings(ctx);
  if (pills.length === 0) return null;

  return (
    <div className={inspectorStyles.runtime.inline}>{pills.join(" · ")}</div>
  );
}

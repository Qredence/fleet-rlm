import type { RuntimeContext } from "@/screens/workspace/use-workspace";

function formatExecutionProfileBadge(executionProfile: RuntimeContext["executionProfile"]) {
  if (executionProfile === "ROOT_INTERLOCUTOR") return undefined;
  return executionProfile.toLowerCase().replace(/_/g, " ");
}

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
  if (ctx.sandboxTransition) pills.push(ctx.sandboxTransition);
  if (ctx.sandboxActive) pills.push("sandbox");
  if (ctx.sandboxId) pills.push(`sandbox ${ctx.sandboxId.slice(0, 10)}`);
  if (ctx.volumeName) pills.push(ctx.volumeName);
  const executionProfileBadge = formatExecutionProfileBadge(ctx.executionProfile);
  if (executionProfileBadge) pills.push(executionProfileBadge);
  return pills;
}

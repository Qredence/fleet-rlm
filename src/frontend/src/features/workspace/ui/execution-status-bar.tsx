import { AnimatePresence, motion } from "motion/react";

import { useWorkspaceUiStore } from "@/lib/workspace/workspace-ui-store";
import { springs } from "@/lib/utils/motion";
import type { WsRuntimeContext } from "@/lib/rlm-api/ws-types";

// ── Depth pill ───────────────────────────────────────────────────────

function depthColor(depth: number, maxDepth: number): string {
  if (maxDepth <= 0) return "text-muted-foreground";
  const ratio = depth / maxDepth;
  if (ratio < 0.5) return "text-emerald-500";
  if (ratio < 0.8) return "text-amber-500";
  return "text-red-500";
}

function depthRingColor(depth: number, maxDepth: number): string {
  if (maxDepth <= 0) return "border-muted-foreground/30";
  const ratio = depth / maxDepth;
  if (ratio < 0.5) return "border-emerald-500/60";
  if (ratio < 0.8) return "border-amber-500/60";
  return "border-red-500/60";
}

interface DepthPillProps {
  depth: number;
  maxDepth: number;
}

function DepthPill({ depth, maxDepth }: DepthPillProps) {
  const textColor = depthColor(depth, maxDepth);
  const ringColor = depthRingColor(depth, maxDepth);
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[10px] leading-none ${ringColor}`}
    >
      <span className="text-muted-foreground/60">depth</span>
      <motion.span
        key={depth}
        className={textColor}
        initial={{ opacity: 0, y: -4 }}
        animate={{ opacity: 1, y: 0 }}
        transition={springs.snappy}
      >
        {depth}/{maxDepth}
      </motion.span>
    </span>
  );
}

// ── Sandbox pill ─────────────────────────────────────────────────────

interface SandboxPillProps {
  active: boolean;
  transition?: string;
}

function SandboxPill({ active, transition }: SandboxPillProps) {
  const isStarting =
    transition === "starting" || transition === "provisioning" || transition === "booting";

  const indicator = active ? "●" : isStarting ? "○" : "◌";
  const indicatorColor = active
    ? "text-emerald-500"
    : isStarting
      ? "text-amber-500"
      : "text-muted-foreground/40";
  const label = active ? "active" : isStarting ? "starting" : "idle";

  return (
    <span className="inline-flex items-center gap-1 rounded border border-border/40 px-1.5 py-0.5 font-mono text-[10px] leading-none text-muted-foreground">
      <motion.span
        key={label}
        className={indicatorColor}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={springs.snappy}
      >
        {indicator}
      </motion.span>
      <span>{label}</span>
    </span>
  );
}

// ── Mode pill ────────────────────────────────────────────────────────

interface ModePillProps {
  mode: string;
}

function ModePill({ mode }: ModePillProps) {
  return (
    <span className="inline-flex items-center rounded border border-border/40 px-1.5 py-0.5 font-mono text-[10px] leading-none text-muted-foreground/70">
      {mode}
    </span>
  );
}

// ── Status bar ───────────────────────────────────────────────────────

interface ExecutionStatusBarInnerProps {
  ctx: WsRuntimeContext;
}

function ExecutionStatusBarInner({ ctx }: ExecutionStatusBarInnerProps) {
  return (
    <div
      data-slot="execution-status-bar"
      className="flex h-8 items-center gap-2 px-2 py-1"
      aria-label="Execution status"
    >
      <DepthPill depth={ctx.depth} maxDepth={ctx.max_depth} />
      <SandboxPill active={ctx.sandbox_active} transition={ctx.sandbox_transition} />
      {ctx.execution_mode ? <ModePill mode={ctx.execution_mode} /> : null}
    </div>
  );
}

/**
 * ExecutionStatusBar — thin (~32px) live status bar rendered below the
 * composer only while a runtime context is active (i.e. streaming).
 *
 * Subscribes to `runtimeContext` from the workspace UI store, which is
 * populated by `applyWsFrameToMessages` on each incoming WS frame.
 */
export function ExecutionStatusBar() {
  const runtimeContext = useWorkspaceUiStore((state) => state.runtimeContext);

  return (
    <AnimatePresence>
      {runtimeContext != null ? (
        <motion.div
          key="execution-status-bar"
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: "auto" }}
          exit={{ opacity: 0, height: 0 }}
          transition={springs.default}
          className="overflow-hidden"
        >
          <ExecutionStatusBarInner ctx={runtimeContext} />
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

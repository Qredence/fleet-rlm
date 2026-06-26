import type { ReactNode } from "react";
import { memo } from "react";
import { AlertCircle, Code2, MessageSquare, Terminal, Wrench } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

import { inspectorInsetClass } from "./inspector-styles";

export type SandboxEventCategory = "code_exec" | "tool_call" | "output" | "error" | "status";

const _CATEGORY_ICONS: Record<SandboxEventCategory, LucideIcon> = {
  code_exec: Code2,
  tool_call: Wrench,
  output: MessageSquare,
  error: AlertCircle,
  status: Terminal,
};

const _CATEGORY_LABELS: Record<SandboxEventCategory, string> = {
  code_exec: "Code Execution",
  tool_call: "Tool Call",
  output: "Output",
  error: "Error",
  status: "Status",
};

const _CATEGORY_TONES: Record<
  SandboxEventCategory,
  "default" | "warning" | "destructive" | "secondary"
> = {
  code_exec: "default",
  tool_call: "secondary",
  output: "default",
  error: "destructive",
  status: "secondary",
};

function CategoryIcon({ category }: { category: SandboxEventCategory }) {
  const Icon = _CATEGORY_ICONS[category] ?? Terminal;
  return <Icon className="size-4 shrink-0" />;
}

export interface SandboxEventRendererProps {
  category: SandboxEventCategory;
  message: string;
  details?: Record<string, unknown>;
  className?: string;
}

/**
 * SandboxEventRenderer — renders a single categorized sandbox event
 * within the trajectory chain, displaying category icon, message, and
 * optional details.
 */
export const SandboxEventRenderer = memo(function SandboxEventRenderer({
  category,
  message,
  details,
  className,
}: SandboxEventRendererProps) {
  const Icon = _CATEGORY_ICONS[category] ?? Terminal;
  const label = _CATEGORY_LABELS[category] ?? "Sandbox Event";
  const tone = _CATEGORY_TONES[category] ?? "default";

  const detailEntries = details ? Object.entries(details) : [];
  const hasDetails = detailEntries.length > 0;

  return (
    <div
      className={cn(
        "space-y-2 rounded-md border border-border-subtle/50 p-3",
        category === "error" && "border-destructive/30 bg-destructive/5",
        className,
      )}
    >
      <div className="flex items-start gap-2">
        <Icon className="size-4 shrink-0 mt-0.5" />
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex items-center gap-2">
            <Badge variant={tone} className="typo-caption">
              {label}
            </Badge>
          </div>
          <div className={cn("text-sm", inspectorInsetClass())}>{message}</div>
        </div>
      </div>
      {hasDetails ? (
        <div className={cn("space-y-1", inspectorInsetClass())}>
          {detailEntries.map(([key, value]) => (
            <div key={key} className="flex items-start gap-2 text-xs">
              <span className="font-mono text-muted-foreground/70 shrink-0">{key}:</span>
              <span className="font-mono text-foreground/90 break-all">{String(value)}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
});

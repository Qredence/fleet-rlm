/**
 * Timeline primitives — reusable step-list visualization.
 *
 * `TimelineStep` is extracted from `ChainOfThoughtStep` so it can be shared
 * between workspace reasoning displays, trajectory views, and any future
 * surface that renders a sequential step-based list with a vertical connector.
 *
 * Usage:
 * ```tsx
 * <div className="flex flex-col">
 *   <TimelineStep label="Fetched context" status="complete" />
 *   <TimelineStep label="Reasoning…" status="active" />
 *   <TimelineStep label="Pending step" status="pending" />
 * </div>
 * ```
 */
import { memo, type ComponentProps, type ReactNode } from "react";
import { DotIcon, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/* -------------------------------------------------------------------------- */
/*                            TimelineStep                                    */
/* -------------------------------------------------------------------------- */

export type TimelineStepStatus = "complete" | "active" | "pending";

const stepStatusStyles: Record<TimelineStepStatus, string> = {
  active: "text-foreground",
  complete: "text-muted-foreground",
  pending: "text-muted-foreground/50",
};

export type TimelineStepProps = ComponentProps<"div"> & {
  /** Optional icon component; defaults to `DotIcon`. */
  icon?: LucideIcon;
  /** Primary label for the step. */
  label: ReactNode;
  /** Optional secondary description shown below the label. */
  description?: ReactNode;
  /** Visual state of the step. Defaults to `"complete"`. */
  status?: TimelineStepStatus;
};

export const TimelineStep = memo(function TimelineStep({
  className,
  icon: Icon = DotIcon,
  label,
  description,
  status = "complete",
  children,
  ...props
}: TimelineStepProps) {
  return (
    <div
      className={cn(
        "flex gap-2 text-sm",
        stepStatusStyles[status],
        "fade-in-0 slide-in-from-top-2 animate-in",
        className,
      )}
      {...props}
    >
      <div className="relative mt-0.5">
        <Icon className="size-4" />
        <div className="absolute top-7 bottom-0 left-1/2 -mx-px w-px bg-border" />
      </div>
      <div className="flex-1 flex flex-col gap-2 overflow-hidden">
        <div>{label}</div>
        {description ? <div className="text-muted-foreground text-xs">{description}</div> : null}
        {children}
      </div>
    </div>
  );
});

/**
 * Execution Status Components
 *
 * Reusable status indicators for workspace execution states.
 * Maps backend execution states to polished UI representations.
 */
import type React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import {
  CheckCircle2,
  CircleDashed,
  Loader2,
  AlertTriangle,
  XCircle,
  Hand,
  Pause,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import type { RunStatus } from "@/lib/workspace/workspace-types";

/* -------------------------------------------------------------------------- */
/*                              Status Badge                                  */
/* -------------------------------------------------------------------------- */

const statusBadgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium transition-colors",
  {
    variants: {
      status: {
        idle: "bg-muted/50 text-muted-foreground",
        bootstrapping: "bg-accent/10 text-accent-foreground",
        running: "bg-primary/10 text-primary",
        completed: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
        error: "bg-destructive/10 text-destructive",
        cancelled: "bg-muted/50 text-muted-foreground",
        needs_human_review: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
        cancelling: "bg-muted/50 text-muted-foreground",
      },
      size: {
        sm: "px-2 py-0.5 text-[11px]",
        default: "px-2.5 py-1 text-xs",
        lg: "px-3 py-1.5 text-sm",
      },
    },
    defaultVariants: {
      status: "idle",
      size: "default",
    },
  },
);

interface StatusBadgeProps extends VariantProps<typeof statusBadgeVariants> {
  status: RunStatus;
  showIcon?: boolean;
  className?: string;
}

const STATUS_ICONS: Record<RunStatus, LucideIcon> = {
  idle: CircleDashed,
  bootstrapping: Loader2,
  running: Loader2,
  completed: CheckCircle2,
  error: XCircle,
  cancelled: Pause,
  needs_human_review: Hand,
  cancelling: Loader2,
};

const STATUS_LABELS: Record<RunStatus, string> = {
  idle: "Ready",
  bootstrapping: "Starting up…",
  running: "Running",
  completed: "Completed",
  error: "Failed",
  cancelled: "Cancelled",
  needs_human_review: "Needs Review",
  cancelling: "Stopping…",
};

export function StatusBadge({ status, size, showIcon = true, className }: StatusBadgeProps) {
  const Icon = STATUS_ICONS[status];
  const label = STATUS_LABELS[status];
  const isAnimated = status === "running" || status === "bootstrapping" || status === "cancelling";

  return (
    <span
      className={cn(statusBadgeVariants({ status, size }), className)}
      role="status"
      aria-label={label}
    >
      {showIcon && (
        <Icon
          className={cn("size-3.5 shrink-0", isAnimated && "animate-spin")}
          aria-hidden="true"
        />
      )}
      <span>{label}</span>
    </span>
  );
}

/* -------------------------------------------------------------------------- */
/*                             Status Indicator                               */
/* -------------------------------------------------------------------------- */

const statusIndicatorVariants = cva("inline-flex size-2 shrink-0 rounded-full", {
  variants: {
    status: {
      idle: "bg-muted-foreground/50",
      bootstrapping: "bg-accent animate-pulse",
      running: "bg-primary animate-pulse",
      completed: "bg-emerald-500",
      error: "bg-destructive",
      cancelled: "bg-muted-foreground/50",
      needs_human_review: "bg-amber-500 animate-pulse",
      cancelling: "bg-muted-foreground/50 animate-pulse",
    },
  },
  defaultVariants: {
    status: "idle",
  },
});

interface StatusIndicatorProps {
  status: RunStatus;
  className?: string;
}

export function StatusIndicator({ status, className }: StatusIndicatorProps) {
  return (
    <span
      className={cn(statusIndicatorVariants({ status }), className)}
      role="status"
      aria-label={STATUS_LABELS[status]}
    />
  );
}

/* -------------------------------------------------------------------------- */
/*                            Status Message Box                              */
/* -------------------------------------------------------------------------- */

const statusMessageVariants = cva("flex items-start gap-3 rounded-lg border p-4 text-sm", {
  variants: {
    variant: {
      info: "border-border bg-muted/30 text-foreground",
      success: "border-emerald-500/30 bg-emerald-500/5 text-foreground",
      warning: "border-amber-500/30 bg-amber-500/5 text-foreground",
      error: "border-destructive/30 bg-destructive/5 text-foreground",
      action: "border-primary/30 bg-primary/5 text-foreground",
    },
  },
  defaultVariants: {
    variant: "info",
  },
});

interface StatusMessageProps extends VariantProps<typeof statusMessageVariants> {
  title?: string;
  children: React.ReactNode;
  icon?: LucideIcon;
  className?: string;
}

const VARIANT_ICONS: Record<NonNullable<StatusMessageProps["variant"]>, LucideIcon> = {
  info: CircleDashed,
  success: CheckCircle2,
  warning: AlertTriangle,
  error: XCircle,
  action: Hand,
};

export function StatusMessage({
  title,
  children,
  variant = "info",
  icon,
  className,
}: StatusMessageProps) {
  const Icon = icon ?? VARIANT_ICONS[variant ?? "info"];

  return (
    <div className={cn(statusMessageVariants({ variant }), className)} role="status">
      <Icon
        className={cn(
          "mt-0.5 size-4 shrink-0",
          variant === "success" && "text-emerald-600 dark:text-emerald-400",
          variant === "warning" && "text-amber-600 dark:text-amber-400",
          variant === "error" && "text-destructive",
          variant === "action" && "text-primary",
          variant === "info" && "text-muted-foreground",
        )}
        aria-hidden="true"
      />
      <div className="flex-1 space-y-1">
        {title && <p className="font-medium leading-none">{title}</p>}
        <div className="text-muted-foreground">{children}</div>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*                          Execution Progress Bar                            */
/* -------------------------------------------------------------------------- */

interface ExecutionProgressProps {
  status: RunStatus;
  className?: string;
}

export function ExecutionProgress({ status, className }: ExecutionProgressProps) {
  const isActive = status === "running" || status === "bootstrapping";
  const isComplete = status === "completed";
  const isError = status === "error";

  if (status === "idle" || status === "cancelled") {
    return null;
  }

  return (
    <div
      className={cn("h-1 w-full overflow-hidden rounded-full bg-muted", className)}
      role="progressbar"
      aria-label="Execution progress"
      aria-valuenow={isComplete ? 100 : isError ? 0 : undefined}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className={cn(
          "h-full rounded-full transition-all duration-500",
          isActive && "animate-progress-indeterminate bg-primary",
          isComplete && "w-full bg-emerald-500",
          isError && "w-full bg-destructive",
          status === "needs_human_review" && "w-3/4 bg-amber-500",
        )}
      />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*                              Helper exports                                */
/* -------------------------------------------------------------------------- */

export { STATUS_ICONS, STATUS_LABELS };
export type { RunStatus };

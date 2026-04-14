/**
 * StateNotice — centered empty/notice state for lists and panels.
 *
 * Use when a list, table, or panel has no content to display.
 *
 * ```tsx
 * <StateNotice
 *   icon={<MessageSquare className="size-10 text-muted-foreground/40" />}
 *   title="No sessions yet"
 *   description="Start a conversation in the Workbench to create your first session."
 *   action={<Button variant="ghost" size="sm">Open Workbench</Button>}
 * />
 * ```
 */
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface StateNoticeProps {
  /** Optional icon or emoji rendered above the title. */
  icon?: ReactNode;
  /** Primary message, e.g. "No sessions yet". */
  title: string;
  /** Optional secondary description below the title. */
  description?: string;
  /** Optional action slot (button, link, etc.). */
  action?: ReactNode;
  className?: string;
  /** Optional className for the title element. */
  titleClassName?: string;
}

export function StateNotice({ icon, title, description, action, className, titleClassName }: StateNoticeProps) {
  return (
    <div className={cn("flex flex-col items-center gap-2 py-12 text-center", className)}>
      {icon ? <div className="mb-1">{icon}</div> : null}
      <p className={cn("text-sm font-medium", titleClassName)}>{title}</p>
      {description ? (
        <p className="text-xs text-muted-foreground">{description}</p>
      ) : null}
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  );
}

/**
 * Queue — AI Elements-inspired compound component for progressively-revealed
 * task lists. Built on top of Radix Collapsible primitives.
 *
 * Fully adapted to the project's design system:
 *   - All typography via `typo` helper (CSS variable refs)
 *   - All colors via Tailwind token classes / `var(--*)` inline
 *   - `data-slot` attributes on every sub-component
 *   - Plain function declarations (HMR-safe)
 *
 * Compound API:
 *   <Queue>
 *     <QueueSection defaultOpen>
 *       <QueueSectionTrigger>
 *         <QueueSectionLabel label="Steps" count={3} />
 *       </QueueSectionTrigger>
 *       <QueueSectionContent>
 *         <QueueList>
 *           <QueueItem>
 *             <QueueItemIndicator completed />
 *             <QueueItemContent completed>Step done</QueueItemContent>
 *             <QueueItemDescription completed>Details</QueueItemDescription>
 *           </QueueItem>
 *         </QueueList>
 *       </QueueSectionContent>
 *     </QueueSection>
 *   </Queue>
 */

import type { ReactNode } from "react";
import { Check, ChevronRight } from "lucide-react";
import { Collapsible, CollapsibleTrigger, CollapsibleContent } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";

// ── Queue (root) ────────────────────────────────────────────────────

interface QueueProps {
  children: ReactNode;
  className?: string;
}

function Queue({ children, className }: QueueProps) {
  return (
    <div data-slot="queue" className={cn("space-y-2", className)}>
      {children}
    </div>
  );
}

// ── QueueSection (collapsible group) ────────────────────────────────

interface QueueSectionProps {
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
}

function QueueSection({ children, defaultOpen = true, className }: QueueSectionProps) {
  return (
    <Collapsible defaultOpen={defaultOpen}>
      <div
        data-slot="queue-section"
        className={cn("rounded-xl border-subtle/80 bg-card/70 overflow-hidden", className)}
      >
        {children}
      </div>
    </Collapsible>
  );
}

// ── QueueSectionTrigger ─────────────────────────────────────────────

interface QueueSectionTriggerProps {
  children: ReactNode;
  className?: string;
}

function QueueSectionTrigger({ children, className }: QueueSectionTriggerProps) {
  return (
    <CollapsibleTrigger asChild>
      <button
        type="button"
        data-slot="queue-section-trigger"
        className={cn(
          "flex items-center gap-2 w-full px-3 py-2.5 group",
          "hover:bg-muted/20 transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
          className,
        )}
        aria-label="Toggle section"
      >
        <ChevronRight
          className="size-3.5 text-muted-foreground shrink-0 transition-transform group-data-[state=open]:rotate-90"
          aria-hidden="true"
        />
        {children}
      </button>
    </CollapsibleTrigger>
  );
}

// ── QueueSectionLabel ───────────────────────────────────────────────

interface QueueSectionLabelProps {
  label: string;
  count?: number;
  className?: string;
}

function QueueSectionLabel({ label, count, className }: QueueSectionLabelProps) {
  return (
    <span data-slot="queue-section-label" className={cn("flex items-center gap-2", className)}>
      <span className="text-foreground typo-label">{label}</span>
      {count != null && <span className="text-muted-foreground typo-helper">{count}</span>}
    </span>
  );
}

// ── QueueSectionContent ─────────────────────────────────────────────

interface QueueSectionContentProps {
  children: ReactNode;
  className?: string;
}

function QueueSectionContent({ children, className }: QueueSectionContentProps) {
  return (
    <CollapsibleContent>
      <div
        data-slot="queue-section-content"
        className={cn("border-t border-border-subtle/80", className)}
      >
        {children}
      </div>
    </CollapsibleContent>
  );
}

// ── QueueList ───────────────────────────────────────────────────────

interface QueueListProps {
  children: ReactNode;
  className?: string;
}

function QueueList({ children, className }: QueueListProps) {
  return (
    <ul
      data-slot="queue-list"
      role="list"
      className={cn("divide-y divide-border-subtle/80", className)}
    >
      {children}
    </ul>
  );
}

// ── QueueItem ───────────────────────────────────────────────────────

interface QueueItemProps {
  children: ReactNode;
  className?: string;
}

function QueueItem({ children, className }: QueueItemProps) {
  return (
    <li
      data-slot="queue-item"
      className={cn("flex flex-wrap items-start gap-2.5 px-3 py-2.5", className)}
    >
      {children}
    </li>
  );
}

// ── QueueItemIndicator ──────────────────────────────────────────────

interface QueueItemIndicatorProps {
  completed?: boolean;
  className?: string;
}

function QueueItemIndicator({ completed = false, className }: QueueItemIndicatorProps) {
  return (
    <div
      data-slot="queue-item-indicator"
      className={cn(
        "size-4 rounded-full flex items-center justify-center shrink-0 mt-px transition-colors",
        completed ? "bg-muted/40" : "border border-border-strong/70",
        className,
      )}
    >
      {completed && (
        <Check className="size-2.5 text-muted-foreground" strokeWidth={3} aria-hidden="true" />
      )}
    </div>
  );
}

// ── QueueItemContent ────────────────────────────────────────────────

interface QueueItemContentProps {
  children: ReactNode;
  completed?: boolean;
  className?: string;
}

function QueueItemContent({ children, completed, className }: QueueItemContentProps) {
  return (
    <span
      data-slot="queue-item-content"
      className={cn(
        "flex-1 min-w-0",
        completed ? "text-foreground" : "text-foreground",
        className,
        "typo-label",
      )}
    >
      {children}
    </span>
  );
}

// ── QueueItemDescription ────────────────────────────────────────────

interface QueueItemDescriptionProps {
  children: ReactNode;
  completed?: boolean;
  className?: string;
}

function QueueItemDescription({ children, completed, className }: QueueItemDescriptionProps) {
  return (
    <span
      data-slot="queue-item-description"
      className={cn(
        "w-full pl-6",
        completed ? "text-muted-foreground" : "text-muted-foreground/70",
        className,
        "typo-caption",
      )}
    >
      {children}
    </span>
  );
}

// ── Exports ─────────────────────────────────────────────────────────

export {
  Queue,
  QueueSection,
  QueueSectionTrigger,
  QueueSectionLabel,
  QueueSectionContent,
  QueueList,
  QueueItem,
  QueueItemIndicator,
  QueueItemContent,
  QueueItemDescription,
};

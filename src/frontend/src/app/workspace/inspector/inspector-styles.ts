/**
 * Shared style tokens for inspector surfaces.
 *
 * All message-inspector tabs, assistant-content previews, and run-workbench
 * panels import these constants so every tab—current or future—renders with
 * the same visual language.
 *
 * Usage:
 *   import { inspectorStyles, inspectorInsetClass, inspectorPreviewButtonClass } from "@/app/workspace/inspector/inspector-styles";
 *
 *   <Card className={inspectorStyles.card.root}>
 *     <CardHeader className={inspectorStyles.card.header}>…</CardHeader>
 *     <CardContent className={inspectorStyles.card.contentStack}>…</CardContent>
 *   </Card>
 */

import { cn } from "@/lib/utils";

export const inspectorStyles = {
  /** Top-level TabsContent inner wrapper. */
  tab: {
    content: "space-y-4 px-4 pb-4",
  },

  /** Vertical stacking scales. */
  stack: {
    /** Section-level: heading + children. */
    section: "space-y-2",
    /** Repeated card list. */
    cards: "space-y-3",
    /** Inner card content stacking. */
    content: "space-y-3",
    /** Dense detail stacking. */
    compact: "space-y-2",
  },

  /** Text headings. */
  heading: {
    /** Section heading (e.g. "Citations", "Tool sessions", "Execution"). */
    section: "text-[11px] uppercase tracking-[0.18em] text-muted-foreground",
    /** Micro label inside detail blocks (e.g. "Input", "Output", "Code"). */
    detail: "text-[10px] uppercase tracking-[0.16em] text-muted-foreground",
  },

  /** Card chrome. */
  card: {
    /** Outer card shell. */
    root: "gap-3 rounded-2xl border-border-subtle/80 shadow-none",
    /** Card header padding. */
    header: "px-4 pt-4",
    /** Card content padding (no internal stack). */
    content: "px-4 pb-4",
    /** Card content padding with stacking. */
    contentStack: "space-y-3 px-4 pb-4",
  },

  /** Inset detail blocks (rounded-xl). Combine with a tone helper. */
  inset: {
    root: "rounded-xl border border-border-subtle/80 px-3 py-2",
    default: "bg-muted/20 text-muted-foreground",
    strong: "bg-muted/20 text-foreground",
    error: "border-destructive/20 bg-destructive/5 text-destructive",
  },

  /** Badge presentation. */
  badge: {
    /** Status badges: Pending / Running / Completed / Failed. */
    status: "rounded-full",
    /** Meta/runtime/count badges. */
    meta: "rounded-full text-xs font-medium",
    /** Standard badge row. */
    row: "flex flex-wrap gap-1.5",
  },

  /** Preview button surfaces (chat-inline teasers). */
  preview: {
    button:
      "w-full rounded-2xl border border-border-subtle/80 bg-muted/18 px-3.5 py-3 text-left transition-colors hover:border-border-strong hover:bg-muted/28",
    selected: "border-accent/35 bg-accent/7",
  },

  /** Runtime context inline text (pill-joined with " · "). */
  runtime: {
    inline: "text-[10px] leading-relaxed text-muted-foreground",
  },

  /** Graph tab specifics. */
  graph: {
    canvas: "h-105 overflow-hidden rounded-2xl border border-border-subtle/80 bg-muted/15",
    statsGrid: "grid gap-3 md:grid-cols-3",
  },
} as const;

/** Build an inset detail-block className for a given tone. */
export function inspectorInsetClass(tone: "default" | "strong" | "error" = "default") {
  return cn(
    inspectorStyles.inset.root,
    tone === "error"
      ? inspectorStyles.inset.error
      : tone === "strong"
        ? inspectorStyles.inset.strong
        : inspectorStyles.inset.default,
  );
}

/** Build a preview button className, optionally selected. */
export function inspectorPreviewButtonClass(selected = false) {
  return cn(inspectorStyles.preview.button, selected && inspectorStyles.preview.selected);
}

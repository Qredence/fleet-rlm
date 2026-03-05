import type { ReactNode } from "react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils/cn";

// ── Types ───────────────────────────────────────────────────────────

interface SelectableCardProps {
  /** Enable selection-mode visual treatment (cursor, click handling). */
  selectable?: boolean;
  /** Whether this card is currently selected. */
  selected?: boolean;
  /** Callback fired when the card is clicked in selection mode. */
  onSelect?: () => void;
  /** Extra highlight variant applied regardless of selection (e.g. pinned). */
  highlighted?: boolean;
  /** Additional class names merged onto the outer Card. */
  className?: string;
  children: ReactNode;
}

// ── Component ───────────────────────────────────────────────────────

/**
 * SelectableCard -- a shadcn Card enhanced with selection-mode styling.
 *
 * When `selectable` is true, the card becomes clickable and shows a
 * `cursor-pointer`. When `selected` is also true it receives a prominent
 * primary border + tinted background.
 *
 * The `highlighted` prop applies an accent highlight independently of
 * selection (e.g. for "pinned" items).
 *
 * All visual tokens come from CSS custom properties so the look can be
 * re-themed from `/src/styles/theme.css` alone.
 *
 * @example
 * ```tsx
 * <SelectableCard
 *   selectable={selectionMode}
 *   selected={selectedIds.has(item.id)}
 *   onSelect={() => toggle(item.id)}
 *   highlighted={item.pinned}
 * >
 *   <CardContent>...</CardContent>
 * </SelectableCard>
 * ```
 */
function SelectableCard({
  selectable = false,
  selected = false,
  onSelect,
  highlighted = false,
  className,
  children,
}: SelectableCardProps) {
  return (
    <Card
      data-slot="selectable-card"
      className={cn(
        "border-border-subtle group transition-colors",
        highlighted && !selected && "border-accent/30 bg-accent/2",
        selectable && selected && "border-primary/40 bg-primary/3",
        selectable && "cursor-pointer",
        className,
      )}
      onClick={selectable ? onSelect : undefined}
    >
      {children}
    </Card>
  );
}

export { SelectableCard };
export type { SelectableCardProps };

import { memo } from "react";
import type { ReactNode } from "react";
import { cn } from "../ui/utils";

// ── Types ───────────────────────────────────────────────────────────

interface ListRowProps {
  /** Leading visual (avatar, icon container, checkbox, etc.). */
  leading?: ReactNode;
  /** Primary label text. */
  label: ReactNode;
  /** Optional secondary text below the label. */
  subtitle?: ReactNode;
  /** Trailing content (badge, actions menu, button, etc.). */
  trailing?: ReactNode;
  /** When true, omits the bottom border. */
  noBorder?: boolean;
  /** Additional class names merged onto the outer container. */
  className?: string;
}

// ── Component ───────────────────────────────────────────────────────

/**
 * ListRow -- a compact horizontal row for item lists.
 *
 * Renders: `[leading] [label + subtitle] [trailing]`
 *
 * Typography is driven by `data-slot` selectors in `theme.css`
 * (`list-row-label` and `list-row-subtitle`), so the look can be
 * re-themed from CSS alone.
 *
 * @example
 * ```tsx
 * <ListRow
 *   leading={<Avatar>...</Avatar>}
 *   label="Alex Chen"
 *   subtitle="alex@qredence.ai"
 *   trailing={<Badge>Admin</Badge>}
 * />
 * ```
 */
const ListRow = memo(function ListRow({
  leading,
  label,
  subtitle,
  trailing,
  noBorder = false,
  className,
}: ListRowProps) {
  return (
    <div
      data-slot="list-row"
      className={cn(
        "flex items-center gap-3 py-2.5",
        !noBorder && "border-b border-border-subtle",
        className,
      )}
    >
      {leading}
      <div className="flex-1 min-w-0">
        <span
          data-slot="list-row-label"
          className="text-foreground block truncate"
        >
          {label}
        </span>
        {subtitle && (
          <span
            data-slot="list-row-subtitle"
            className="text-muted-foreground block truncate"
          >
            {subtitle}
          </span>
        )}
      </div>
      {trailing}
    </div>
  );
});

export { ListRow };
export type { ListRowProps };

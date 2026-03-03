import type { ReactNode } from "react";
import { cn } from "@/components/ui/utils";

// ── Types ───────────────────────────────────────────────────────────

interface ResolvedChipProps {
  /** Optional leading icon (e.g. `<Check strokeWidth={3} />`). Auto-sized to 14×14. */
  icon?: ReactNode;
  /** Chip label content. */
  children: ReactNode;
  /** Additional class names merged onto the outer container. */
  className?: string;
}

// ── Component ───────────────────────────────────────────────────────

/**
 * ResolvedChip — a compact, muted pill that shows a resolved/confirmed
 * answer in HITL and clarification flows.
 *
 * Typography is utility-class driven for consistency across themes.
 *
 * @example
 * ```tsx
 * // Simple label-only chip
 * <ResolvedChip>{data.resolvedAnswer}</ResolvedChip>
 *
 * // With a check icon
 * <ResolvedChip icon={<Check strokeWidth={3} />}>{data.resolvedLabel}</ResolvedChip>
 * ```
 */
function ResolvedChip({ icon, children, className }: ResolvedChipProps) {
  return (
    <div
      data-slot="resolved-chip"
      className={cn(
        "inline-flex items-center gap-2 px-3 py-1.5 rounded-md bg-muted border border-border-subtle text-foreground w-fit",
        className,
      )}
    >
      {icon && (
        <span
          className="shrink-0 [&_svg]:w-3.5 [&_svg]:h-3.5"
          aria-hidden="true"
        >
          {icon}
        </span>
      )}
      <span
        data-slot="resolved-chip-label"
        className="text-sm font-medium leading-5"
      >
        {children}
      </span>
    </div>
  );
}

export { ResolvedChip };
export type { ResolvedChipProps };

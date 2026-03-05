import type { ReactNode } from "react";
import { cn } from "@/lib/utils/cn";

// ── Types ───────────────────────────────────────────────────────────

interface SectionHeaderProps {
  /** Leading icon element (auto-sized to 16×16 via [&_svg] selector). */
  icon: ReactNode;
  /** Label content — can be a string or styled ReactNode. */
  children: ReactNode;
  /** Additional class names merged onto the outer row (e.g. `mb-3`). */
  className?: string;
}

// ── Component ───────────────────────────────────────────────────────

/**
 * SectionHeader — a compact icon + label row used at the top of cards,
 * sections, and inline group headers.
 *
 * Typography is **not** enforced here — the consumer passes styled
 * `children` (using `typo.*` or `data-slot`) so the component stays
 * layout-only, maximising reuse across visual variants.
 *
 * Icon sizing is standardised to `w-4 h-4` via `[&_svg]` selectors.
 *
 * @example
 * ```tsx
 * <SectionHeader icon={<MessageSquare className="text-muted-foreground" />} className="mb-1">
 *   <span className="text-muted-foreground" style={typo.helper}>{data.stepLabel}</span>
 * </SectionHeader>
 * ```
 */
function SectionHeader({ icon, children, className }: SectionHeaderProps) {
  return (
    <div
      data-slot="section-header"
      className={cn("flex items-center gap-2", className)}
    >
      <span className="shrink-0 [&_svg]:w-4 [&_svg]:h-4" aria-hidden="true">
        {icon}
      </span>
      {children}
    </div>
  );
}

export { SectionHeader };
export type { SectionHeaderProps };

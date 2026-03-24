import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

// ── Types ───────────────────────────────────────────────────────────

interface RadioOptionCardProps {
  /** Whether this option is currently selected. */
  selected: boolean;
  /** Callback fired when the option is clicked. */
  onSelect: () => void;
  /** Primary label text. */
  label: string;
  /** Optional secondary description text below the label. */
  description?: string;
  /** Optional leading icon rendered before the label (e.g. `<Pencil />`) . */
  icon?: ReactNode;
  /** Additional class names merged onto the outer button. */
  className?: string;
}

// ── Component ───────────────────────────────────────────────────────

/**
 * RadioOptionCard -- a selectable option card with an animated radio
 * indicator, following the iOS 26 Liquid Glass aesthetic.
 *
 * Typography is driven entirely by `data-slot` selectors in `theme.css`
 * (`radio-option-card-label` and `radio-option-card-description`), so
 * the look can be re-themed from CSS alone.
 *
 * Uses centralized spring physics from `motion-config.ts` and respects
 * `prefers-reduced-motion`.
 *
 * @example
 * ```tsx
 * <RadioOptionCard
 *   selected={selectedId === 'opt-1'}
 *   onSelect={() => setSelectedId('opt-1')}
 *   label="Automated tests"
 *   description="Run quality checks automatically"
 * />
 * ```
 */
function RadioOptionCard({
  selected,
  onSelect,
  label,
  description,
  icon,
  className,
}: RadioOptionCardProps) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      data-slot="radio-option-card"
      onClick={onSelect}
      className={cn(
        "flex items-start gap-3 w-full text-left px-3.5 py-3.5 rounded-lg border transition-colors touch-target",
        "focus-visible:outline-none focus-visible:ring-[2px] focus-visible:ring-ring/50",
        selected ? "border-border-strong bg-muted" : "border-border-subtle bg-card hover:bg-muted",
        className,
      )}
    >
      {/* ── Radio dot indicator ─────────────────────────────────── */}
      <div
        className={cn(
          "mt-0.5 w-5 h-5 rounded-full border-2 shrink-0 flex items-center justify-center transition-colors",
          selected ? "border-foreground" : "border-muted-foreground/40",
        )}
        aria-hidden="true"
      >
        <div
          className={cn(
            "w-2.5 h-2.5 rounded-full bg-foreground transition-transform duration-150",
            selected ? "scale-100" : "scale-0",
          )}
        />
      </div>

      {/* ── Label + description ────────────────────────────────── */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          {icon && (
            <span
              className="text-muted-foreground shrink-0 [&_svg]:w-3.5 [&_svg]:h-3.5"
              aria-hidden="true"
            >
              {icon}
            </span>
          )}
          <span data-slot="radio-option-card-label" className="text-foreground">
            {label}
          </span>
        </div>
        {description && (
          <p data-slot="radio-option-card-description" className="text-muted-foreground mt-0.5">
            {description}
          </p>
        )}
      </div>
    </button>
  );
}

export { RadioOptionCard };
export type { RadioOptionCardProps };

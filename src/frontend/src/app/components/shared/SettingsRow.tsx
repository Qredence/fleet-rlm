import { memo } from "react";
import type { ReactNode } from "react";
import { cn } from "../ui/utils";

// ── Types ───────────────────────────────────────────────────────────

interface SettingsRowProps {
  /** Primary label text. */
  label: string;
  /** Optional secondary description below the label. */
  description?: string;
  /** Trailing content (toggle, select, badge, button group, etc.). */
  children?: ReactNode;
  /** When true, omits the bottom border (use for the last row in a group). */
  noBorder?: boolean;
  /** Additional class names merged onto the outer container. */
  className?: string;
}

// ── Component ───────────────────────────────────────────────────────

/**
 * SettingsRow -- the canonical horizontal settings row layout.
 *
 * Label + optional description on the left; arbitrary trailing content
 * on the right via `children`. Renders `border-b border-border-subtle`
 * by default; pass `noBorder` to suppress for the final row.
 *
 * Typography is driven entirely by `data-slot` selectors in `theme.css`
 * (`settings-row-label` and `settings-row-description`), so the look
 * can be re-themed from CSS alone.
 *
 * @example
 * ```tsx
 * <SettingsRow label="Language" description="Select your preferred language.">
 *   <Select value={language} onValueChange={setLanguage}>...</Select>
 * </SettingsRow>
 *
 * <SettingsRow label="Version">
 *   <span data-slot="settings-row-value">v0.9.0-beta</span>
 * </SettingsRow>
 * ```
 */
const SettingsRow = memo(function SettingsRow({
  label,
  description,
  children,
  noBorder = false,
  className,
}: SettingsRowProps) {
  return (
    <div
      data-slot="settings-row"
      className={cn(
        "flex items-center justify-between py-4",
        !noBorder && "border-b border-border-subtle",
        className,
      )}
    >
      <div className="flex-1 min-w-0 mr-4">
        <span data-slot="settings-row-label" className="text-foreground">
          {label}
        </span>
        {description && (
          <p
            data-slot="settings-row-description"
            className="text-muted-foreground mt-0.5"
          >
            {description}
          </p>
        )}
      </div>
      {children}
    </div>
  );
});

export { SettingsRow };
export type { SettingsRowProps };

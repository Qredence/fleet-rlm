import { type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils/cn";
import { typo } from "@/lib/config/typo";
import { Badge } from "@/components/ui/badge";

/* ── Types ─────────────────────────────────────────────────────────── */

export interface PanelHeaderChipProps {
  /** Leading icon element (e.g. a Lucide icon). */
  icon?: ReactNode;
  /** Primary label text. */
  label: string;
  /** Optional version string — rendered as a mono Badge when present. */
  version?: string;
  /** Whether to show the trailing chevron indicator. @default true */
  showChevron?: boolean;
  /** When true, rotates the chevron 180° (popover open state). */
  open?: boolean;
  /** Click handler — makes the chip interactive (button role). */
  onClick?: () => void;
  /**
   * Force interactive visual states (hover, active, cursor) even when no
   * `onClick` is provided. Useful when a parent Radix trigger manages the
   * click and the chip is purely visual.
   */
  interactive?: boolean;
  /** Disabled state — dims the chip and prevents interaction. */
  disabled?: boolean;
  /** Additional class names merged via cn(). */
  className?: string;
}

/* ── Component ─────────────────────────────────────────────────────── */

/**
 * PanelHeaderChip — a pill-shaped breadcrumb chip used in panel headers.
 *
 * Renders as a `<button>` when `onClick` is provided, otherwise a `<div>`.
 * Supports hover, focus-visible, active (pressed), and disabled states,
 * all using design-system CSS variables.
 *
 * Plain function declaration (no forwardRef) — the Figma Make HMR
 * environment breaks with forwardRef const exports. When composing
 * with Radix `asChild` triggers, wrap in `<span className="inline-flex">`
 * so the span (not this component) receives the Radix ref/props.
 *
 * @example
 * ```tsx
 * <PanelHeaderChip
 *   icon={<Brain className="size-3.5 text-accent shrink-0" />}
 *   label="Code Sandbox"
 *   version="1.2.0"
 *   open={isPopoverOpen}
 *   onClick={() => setIsPopoverOpen(o => !o)}
 * />
 * ```
 */
export function PanelHeaderChip({
  icon,
  label,
  version,
  showChevron = true,
  open,
  onClick,
  interactive = false,
  disabled = false,
  className,
}: PanelHeaderChipProps) {
  const isInteractive = (!!onClick || interactive) && !disabled;
  const Tag = onClick ? "button" : "div";

  return (
    <Tag
      data-slot="panel-header-chip"
      type={onClick ? "button" : undefined}
      onClick={isInteractive ? onClick : undefined}
      disabled={onClick ? disabled : undefined}
      className={cn(
        /* ── Base layout ────────────────────────────────────────── */
        "inline-flex items-center gap-2 px-3 py-1.5 min-w-0",
        "bg-secondary rounded-button border border-border-subtle",
        /* ── Transition ─────────────────────────────────────────── */
        "transition-[background-color,border-color,box-shadow,transform]",
        "duration-150 ease-out",
        /* ── Interactive states (only when clickable) ───────────── */
        isInteractive && [
          "cursor-pointer",
          "hover:border-border hover:shadow-sm hover:bg-secondary/80",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
          "active:scale-(--haptic-scale)",
        ],
        /* ── Open state (popover active) ───────────────────────── */
        open && "border-border shadow-sm bg-secondary/80",
        /* ── Disabled ───────────────────────────────────────────── */
        disabled && "opacity-50 cursor-not-allowed",
        className,
      )}
    >
      {/* Leading icon */}
      {icon}

      {/* Label */}
      <span
        data-slot="panel-header-chip-label"
        className="text-foreground truncate"
        style={typo.label}
      >
        {label}
      </span>

      {/* Version badge */}
      {version && (
        <Badge variant="secondary" className="rounded-full" style={typo.mono}>
          v{version}
        </Badge>
      )}

      {/* Trailing chevron */}
      {showChevron && (
        <ChevronDown
          className={cn(
            "size-4 text-muted-foreground shrink-0",
            "transition-transform duration-150",
            open && "rotate-180",
          )}
          aria-hidden="true"
        />
      )}
    </Tag>
  );
}

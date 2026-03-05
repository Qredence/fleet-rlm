import { type ComponentType } from "react";
import { motion, useReducedMotion } from "motion/react";
import { cn } from "@/lib/utils/cn";

// ── Types ───────────────────────────────────────────────────────────

interface SuggestionChipProps {
  /** SVG icon component rendered at leading position (size-4). */
  icon: ComponentType;
  /** Visible label text. */
  label: string;
  /** Zero-based index used for stagger delay calculation. */
  index?: number;
  /** Click handler — receives the label text. */
  onClick?: (label: string) => void;
  /** Extra classes merged via cn(). */
  className?: string;
}

// ── Component ───────────────────────────────────────────────────────

/**
 * SuggestionChip — animated pill button with a leading icon and label.
 *
 * Renders as a `motion.button` with a staggered fade-up entrance.
 * Respects `prefers-reduced-motion` by disabling the translate and
 * collapsing duration.
 *
 * Plain function declaration — `const + forwardRef` crashes HMR in
 * Figma Make preview.
 */
function SuggestionChip({
  icon: Icon,
  label,
  index = 0,
  onClick,
  className,
}: SuggestionChipProps) {
  const prefersReduced = useReducedMotion();

  return (
    <motion.button
      type="button"
      data-slot="suggestion-chip"
      className={cn(
        "flex items-center gap-2 px-4 py-2.5 rounded-button",
        "bg-secondary border border-border-subtle",
        "hover:border-border-strong hover:bg-secondary/80",
        "transition-colors",
        "focus-visible:outline-none focus-visible:ring-[2px] focus-visible:ring-ring/50",
        className,
      )}
      initial={{ opacity: 0, y: prefersReduced ? 0 : 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={
        prefersReduced ? { duration: 0.01 } : { delay: 0.15 + index * 0.08 }
      }
      onClick={() => onClick?.(label)}
    >
      <Icon />
      <span data-slot="suggestion-chip-label" className="text-foreground">
        {label}
      </span>
    </motion.button>
  );
}

export { SuggestionChip };
export type { SuggestionChipProps };

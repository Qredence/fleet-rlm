/**
 * ScoreBadge — Colored score display with threshold-based variants.
 *
 * Maps a numeric score (0–1) to green / amber / red coloring for
 * at-a-glance quality indication in tables, cards, and dashboards.
 *
 * ```tsx
 * <ScoreBadge score={0.82} />           // → green "82%"
 * <ScoreBadge score={0.45} />           // → amber "45%"
 * <ScoreBadge score={0.2} format="decimal" />  // → red "0.20"
 * ```
 */
import { cva } from "class-variance-authority";
import { cn } from "@/lib/utils";

/* -------------------------------------------------------------------------- */
/*                                   Types                                    */
/* -------------------------------------------------------------------------- */

export interface ScoreBadgeProps {
  /** Numeric score, expected in the 0–1 range. */
  score: number;
  /** Display format. Defaults to `"percent"`. */
  format?: "percent" | "decimal";
  className?: string;
}

/* -------------------------------------------------------------------------- */
/*                                 Variants                                   */
/* -------------------------------------------------------------------------- */

type ScoreTone = "success" | "warning" | "danger";

function scoreTone(score: number): ScoreTone {
  if (score >= 0.7) return "success";
  if (score >= 0.4) return "warning";
  return "danger";
}

const scoreBadgeVariants = cva(
  "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums",
  {
    variants: {
      tone: {
        success: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
        warning: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
        danger: "bg-destructive/10 text-destructive",
      },
    },
    defaultVariants: {
      tone: "success",
    },
  },
);

/* -------------------------------------------------------------------------- */
/*                              Component                                     */
/* -------------------------------------------------------------------------- */

export function ScoreBadge({ score, format = "percent", className }: ScoreBadgeProps) {
  const tone = scoreTone(score);
  const display =
    format === "percent"
      ? `${Math.round(score * 100)}%`
      : score.toFixed(2);

  return (
    <span className={cn(scoreBadgeVariants({ tone }), className)} aria-label={`Score: ${display}`}>
      {display}
    </span>
  );
}

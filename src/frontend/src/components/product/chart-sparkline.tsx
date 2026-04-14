/**
 * ChartSparkline — Inline SVG mini-chart for score trends.
 *
 * Renders a lightweight polyline sparkline normalised to the 0–1 range.
 * No external charting library required — pure SVG.
 *
 * ```tsx
 * <ChartSparkline data={[0.3, 0.5, 0.45, 0.7, 0.82]} />
 * ```
 */
import { useMemo } from "react";
import { cn } from "@/lib/utils";

/* -------------------------------------------------------------------------- */
/*                                   Types                                    */
/* -------------------------------------------------------------------------- */

export interface ChartSparklineProps {
  /** Array of numeric values (expected 0–1 range). */
  data: number[];
  /** SVG width in px. Defaults to 80. */
  width?: number;
  /** SVG height in px. Defaults to 24. */
  height?: number;
  /** Stroke color. Defaults to `"currentColor"`. */
  color?: string;
  /** Show a dot on the last data point. Defaults to true. */
  showEndDot?: boolean;
  className?: string;
}

/* -------------------------------------------------------------------------- */
/*                              Component                                     */
/* -------------------------------------------------------------------------- */

const PADDING = 2;

export function ChartSparkline({
  data,
  width = 80,
  height = 24,
  color = "currentColor",
  showEndDot = true,
  className,
}: ChartSparklineProps) {
  const points = useMemo(() => {
    if (data.length === 0) return "";
    const drawW = width - PADDING * 2;
    const drawH = height - PADDING * 2;
    const step = data.length > 1 ? drawW / (data.length - 1) : 0;
    return data
      .map((v, i) => {
        const x = PADDING + i * step;
        const y = PADDING + drawH - Math.max(0, Math.min(1, v)) * drawH;
        return `${x},${y}`;
      })
      .join(" ");
  }, [data, width, height]);

  const lastPoint = useMemo(() => {
    if (data.length === 0) return null;
    const drawW = width - PADDING * 2;
    const drawH = height - PADDING * 2;
    const step = data.length > 1 ? drawW / (data.length - 1) : 0;
    const lastIdx = data.length - 1;
    return {
      x: PADDING + lastIdx * step,
      y: PADDING + drawH - Math.max(0, Math.min(1, data[lastIdx] ?? 0)) * drawH,
    };
  }, [data, width, height]);

  if (data.length === 0) {
    return (
      <span className={cn("inline-block text-xs text-muted-foreground", className)}>—</span>
    );
  }

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      fill="none"
      className={cn("inline-block align-middle", className)}
      aria-hidden="true"
    >
      <polyline
        points={points}
        stroke={color}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {showEndDot && lastPoint && (
        <circle cx={lastPoint.x} cy={lastPoint.y} r={2} fill={color} />
      )}
    </svg>
  );
}

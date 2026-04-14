/**
 * KeyValueGrid — data-driven key-value display grid for metadata panels.
 *
 * Accepts an array of label/value pairs and renders them in a 1- or 2-column
 * grid with consistent styling. Null or undefined values display as "—".
 *
 * ```tsx
 * <KeyValueGrid
 *   items={[
 *     { label: "Status", value: "Running" },
 *     { label: "Duration", value: "2.3s" },
 *     { label: "Error", value: null },
 *   ]}
 * />
 * ```
 */
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface KeyValueGridProps {
  items: Array<{ label: string; value: ReactNode }>;
  /** Number of label-value columns. @default 2 */
  columns?: 1 | 2;
  className?: string;
}

export function KeyValueGrid({ items, columns = 2, className }: KeyValueGridProps) {
  return (
    <dl
      className={cn(
        "grid gap-x-4 gap-y-2 items-baseline",
        columns === 2 && "grid-cols-[auto_1fr_auto_1fr]",
        columns === 1 && "grid-cols-[auto_1fr]",
        className,
      )}
    >
      {items.map((item) => (
        <KeyValuePair key={item.label} label={item.label} value={item.value} />
      ))}
    </dl>
  );
}

function KeyValuePair({ label, value }: { label: string; value: ReactNode }) {
  return (
    <>
      <dt className="text-xs font-medium text-muted-foreground whitespace-nowrap">{label}</dt>
      <dd className="text-sm text-foreground">{value ?? "—"}</dd>
    </>
  );
}

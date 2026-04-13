/**
 * PropertyList — reusable key-value display for detail panels.
 *
 * Used across inspector tabs, settings, volumes, and optimization
 * to render labeled values in a consistent layout.
 *
 * ```tsx
 * <PropertyList>
 *   <PropertyItem label="Status" value="Running" />
 *   <PropertyItem label="Duration" value="2.3s" />
 * </PropertyList>
 * ```
 */
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/* -------------------------------------------------------------------------- */
/*                            PropertyList                                    */
/* -------------------------------------------------------------------------- */

interface PropertyListProps {
  children: ReactNode;
  className?: string;
  /** Orientation — stacked (default) or inline grid */
  variant?: "stacked" | "grid";
}

export function PropertyList({ children, className, variant = "stacked" }: PropertyListProps) {
  return (
    <dl
      className={cn(
        variant === "stacked" && "flex flex-col gap-3",
        variant === "grid" && "grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 items-baseline",
        className,
      )}
    >
      {children}
    </dl>
  );
}

/* -------------------------------------------------------------------------- */
/*                            PropertyItem                                    */
/* -------------------------------------------------------------------------- */

interface PropertyItemProps {
  label: string;
  value?: ReactNode;
  className?: string;
  /** Visual tone for the value text */
  tone?: "default" | "muted" | "error" | "success";
}

const toneClasses: Record<NonNullable<PropertyItemProps["tone"]>, string> = {
  default: "text-foreground",
  muted: "text-muted-foreground",
  error: "text-destructive",
  success: "text-emerald-600 dark:text-emerald-400",
};

export function PropertyItem({ label, value, className, tone = "default" }: PropertyItemProps) {
  if (value == null || value === "") return null;

  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className={cn("text-sm", toneClasses[tone])}>{value}</dd>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*                          PropertyGroup                                     */
/* -------------------------------------------------------------------------- */

interface PropertyGroupProps {
  title?: string;
  children: ReactNode;
  className?: string;
}

export function PropertyGroup({ title, children, className }: PropertyGroupProps) {
  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {title ? (
        <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground/70">
          {title}
        </h4>
      ) : null}
      {children}
    </div>
  );
}

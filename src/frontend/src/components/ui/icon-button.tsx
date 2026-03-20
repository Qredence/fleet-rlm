import * as React from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils/cn";

// ── Types ───────────────────────────────────────────────────────────
interface IconButtonProps extends React.ComponentPropsWithoutRef<typeof Button> {
  /** When true, renders the active/toggled visual state (accent tint). */
  isActive?: boolean;
}

const iconSizeMap: Record<string, string> = {
  sm: "size-3.5",
  default: "size-4",
  icon: "size-4",
  lg: "size-5",
};

function normalizeIconButtonChildren(children: React.ReactNode, size?: string | null) {
  const iconClass = iconSizeMap[size ?? "icon"] ?? "size-4";
  return React.Children.map(children, (child) => {
    if (!React.isValidElement<{ className?: string }>(child)) {
      return child;
    }

    return React.cloneElement(child, {
      className: cn(child.props.className, iconClass, "shrink-0"),
    });
  });
}

// ── Component ───────────────────────────────────────────────────────
/**
 * Token-driven icon button built on the shared Button primitive so
 * icon-only buttons inherit the same 16×16 icon contract as text+icon buttons.
 *
 * Plain function declaration — `const + forwardRef` crashes HMR in
 * Figma Make preview. When used inside `<TooltipTrigger asChild>`,
 * wrap in a native `<span className="inline-flex">` so Radix can
 * attach refs to the span instead.
 */
function IconButton({
  className,
  isActive = false,
  children,
  size,
  variant,
  ...props
}: IconButtonProps) {
  const resolvedVariant = variant ?? "ghost";

  return (
    <Button
      data-slot="icon-button"
      data-active={isActive ? "true" : "false"}
      size={size ?? "icon"}
      variant={resolvedVariant}
      className={cn(
        "rounded-lg focus-visible:ring-2",
        resolvedVariant === "ghost" && "border border-transparent bg-transparent",
        // When active, match the semantic foreground text token.
        // This ensures the computed `color` is driven by `--color-text`.
        "text-foreground",
        className,
      )}
      {...props}
    >
      {normalizeIconButtonChildren(children, size ?? "icon")}
    </Button>
  );
}

export { IconButton };
export type { IconButtonProps };

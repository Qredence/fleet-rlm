import * as React from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils/cn";

// ── Types ───────────────────────────────────────────────────────────
interface IconButtonProps extends React.ComponentPropsWithoutRef<typeof Button> {
  /** When true, renders the active/toggled visual state (accent tint). */
  isActive?: boolean;
}

function normalizeIconButtonChildren(children: React.ReactNode) {
  return React.Children.map(children, (child) => {
    if (!React.isValidElement<{ className?: string }>(child)) {
      return child;
    }

    return React.cloneElement(child, {
      className: cn(child.props.className, "size-4 shrink-0"),
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
        isActive ? "text-accent" : "text-foreground",
        className,
      )}
      {...props}
    >
      {normalizeIconButtonChildren(children)}
    </Button>
  );
}

export { IconButton };
export type { IconButtonProps };

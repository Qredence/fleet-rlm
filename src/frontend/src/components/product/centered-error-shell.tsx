import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * Card-wrapped centered error / empty-state shell.
 * Replaces the duplicated `flex min-h-dvh w-full items-center justify-center
 * bg-background px-6` + `mx-auto flex w-full max-w-xl flex-col items-center
 * rounded-card border border-subtle bg-card px-6 py-10 text-center shadow-sm`
 * recipe across 404, route-error-screen, and auth fallbacks.
 */
export type CenteredErrorShellProps = {
  children: ReactNode;
  className?: string;
  /** Card max-width class; defaults to `max-w-xl`. */
  cardWidth?: string;
};

export function CenteredErrorShell({
  children,
  className,
  cardWidth = "max-w-xl",
}: CenteredErrorShellProps) {
  return (
    <div className="font-app flex min-h-dvh w-full items-center justify-center bg-background px-6">
      <div
        className={cn(
          "mx-auto flex w-full flex-col items-center rounded-card border border-subtle bg-card px-6 py-10 text-center shadow-sm",
          cardWidth,
          className,
        )}
      >
        {children}
      </div>
    </div>
  );
}

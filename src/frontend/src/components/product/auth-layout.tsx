import type { ReactNode } from "react";
import { ArrowLeft } from "lucide-react";
import { useNavigate } from "@tanstack/react-router";

import { BrandMark } from "@/components/brand-mark";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Shared auth surface shell used by the Neon `/login` and `/signup` routes.
 *
 * Renders a centered, full-viewport card with a brand mark, heading, subtitle,
 * an optional back-to-workbench affordance, and a body slot for the Neon
 * `SignInForm` / `SignUpForm`. Purely presentational — no feature imports.
 */
export type AuthLayoutProps = {
  title: string;
  subtitle: string;
  children: ReactNode;
  /** Optional footer rendered below the form body (e.g. sign-in/sign-up link). */
  footer?: ReactNode;
  /** Card max-width class; defaults to `max-w-100`. */
  cardWidth?: string;
  className?: string;
};

export function AuthLayout({
  title,
  subtitle,
  children,
  footer,
  cardWidth = "max-w-100",
  className,
}: AuthLayoutProps) {
  const navigate = useNavigate();

  return (
    <div className="flex min-h-dvh items-center justify-center bg-background px-4 py-8">
      <div
        className={cn(
          "surface-raised-card relative w-full border border-border-subtle p-8",
          cardWidth,
          className,
        )}
      >
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={() => navigate({ to: "/app/workspace" })}
          className="absolute top-4 left-4 h-8 w-8 text-muted-foreground hover:text-foreground"
          aria-label="Back to workbench"
        >
          <ArrowLeft className="size-4" />
        </Button>
        <div className="flex flex-col items-center gap-3 pb-6">
          <BrandMark className="h-3.75 w-8 text-foreground" />
          <div className="text-center">
            <h1 className="text-sm font-medium text-foreground text-balance">{title}</h1>
            <p className="mt-1 text-muted-foreground typo-caption text-pretty">{subtitle}</p>
          </div>
        </div>
        {children}
        {footer ? <div className="mt-6">{footer}</div> : null}
      </div>
    </div>
  );
}

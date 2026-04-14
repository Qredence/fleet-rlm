/**
 * Section Layout Components
 *
 * Composable section wrappers for workspace areas with consistent
 * visual hierarchy, spacing, and responsive behavior.
 */
import type { ReactNode } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/* -------------------------------------------------------------------------- */
/*                              Section Root                                  */
/* -------------------------------------------------------------------------- */

const sectionVariants = cva("flex flex-col", {
  variants: {
    spacing: {
      none: "gap-0",
      tight: "gap-2",
      default: "gap-4",
      loose: "gap-6",
    },
    padding: {
      none: "",
      sm: "p-3",
      default: "p-4 md:p-5",
      lg: "p-6 md:p-8",
    },
  },
  defaultVariants: {
    spacing: "default",
    padding: "none",
  },
});

interface SectionProps extends VariantProps<typeof sectionVariants> {
  children: ReactNode;
  className?: string;
  "data-slot"?: string;
}

export function Section({ children, spacing, padding, className, ...props }: SectionProps) {
  return (
    <section className={cn(sectionVariants({ spacing, padding }), className)} {...props}>
      {children}
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/*                            Section Header                                  */
/* -------------------------------------------------------------------------- */

interface SectionHeaderProps {
  title?: string;
  description?: string;
  action?: ReactNode;
  children?: ReactNode;
  className?: string;
}

export function SectionHeader({
  title,
  description,
  action,
  children,
  className,
}: SectionHeaderProps) {
  return (
    <header className={cn("flex items-start justify-between gap-4", className)}>
      <div className="flex flex-col gap-1">
        {title && <h2 className="text-sm font-medium tracking-tight text-foreground">{title}</h2>}
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
        {children}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </header>
  );
}

/* -------------------------------------------------------------------------- */
/*                            Section Content                                 */
/* -------------------------------------------------------------------------- */

interface SectionContentProps {
  children: ReactNode;
  className?: string;
  scroll?: boolean;
}

export function SectionContent({ children, className, scroll }: SectionContentProps) {
  return (
    <div
      className={cn("flex-1", scroll && "min-h-0 overflow-y-auto overscroll-contain", className)}
    >
      {children}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*                             Section Card                                   */
/* -------------------------------------------------------------------------- */

const sectionCardVariants = cva("rounded-lg border transition-colors", {
  variants: {
    variant: {
      default: "border-border bg-card",
      muted: "border-border-subtle/50 bg-muted/30",
      elevated: "border-border bg-card shadow-sm",
      outline: "border-border bg-transparent",
    },
    interactive: {
      true: "cursor-pointer hover:border-border-subtle hover:bg-accent/5",
      false: "",
    },
  },
  defaultVariants: {
    variant: "default",
    interactive: false,
  },
});

interface SectionCardProps extends VariantProps<typeof sectionCardVariants> {
  children: ReactNode;
  className?: string;
  onClick?: () => void;
}

export function SectionCard({
  children,
  variant,
  interactive,
  className,
  onClick,
}: SectionCardProps) {
  const classes = cn(sectionCardVariants({ variant, interactive }), className);

  if (onClick) {
    return (
      <button type="button" className={classes} onClick={onClick}>
        {children}
      </button>
    );
  }

  return <div className={classes}>{children}</div>;
}

/* -------------------------------------------------------------------------- */
/*                           Content Area                                     */
/* -------------------------------------------------------------------------- */

interface ContentAreaProps {
  children: ReactNode;
  className?: string;
  maxWidth?: "sm" | "md" | "lg" | "xl" | "content" | "full";
  center?: boolean;
}

const maxWidthClasses = {
  sm: "max-w-sm",
  md: "max-w-md",
  lg: "max-w-lg",
  xl: "max-w-xl",
  content: "max-w-content",
  full: "max-w-full",
};

export function ContentArea({
  children,
  className,
  maxWidth = "content",
  center = true,
}: ContentAreaProps) {
  return (
    <div className={cn("w-full", maxWidthClasses[maxWidth], center && "mx-auto", className)}>
      {children}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*                          Workspace Regions                                 */
/* -------------------------------------------------------------------------- */

interface WorkspaceRegionProps {
  children: ReactNode;
  className?: string;
  "data-slot"?: string;
}

/** Main scrollable content region */
export function MainRegion({ children, className, ...props }: WorkspaceRegionProps) {
  return (
    <div className={cn("flex-1 min-h-0 overflow-y-auto overscroll-contain", className)} {...props}>
      {children}
    </div>
  );
}

/** Fixed footer region (e.g., composer) */
export function FooterRegion({ children, className, ...props }: WorkspaceRegionProps) {
  return (
    <div
      className={cn(
        "shrink-0 border-t border-border/50 bg-background/95 backdrop-blur-sm",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

/** Sticky header region */
export function HeaderRegion({ children, className, ...props }: WorkspaceRegionProps) {
  return (
    <div
      className={cn(
        "shrink-0 sticky top-0 z-10 border-b border-border/50 bg-background/95 backdrop-blur-sm",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

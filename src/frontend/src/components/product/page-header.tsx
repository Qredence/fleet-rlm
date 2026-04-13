/**
 * PageHeader — shared page header pattern for feature screens.
 *
 * Provides a consistent top-of-page heading with title, description, and
 * optional children (e.g. search bars, action buttons).
 *
 * Adapts automatically for mobile vs desktop layout, matching the existing
 * header patterns in Volumes and Optimization screens.
 *
 * ```tsx
 * <PageHeader title="Volume Browser" description="Browse mounted volumes.">
 *   <SearchInput />
 * </PageHeader>
 * ```
 */
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { useIsMobile } from "@/hooks/use-is-mobile";

interface PageHeaderProps {
  title: string;
  description?: string;
  children?: ReactNode;
  className?: string;
  /** Maximum content width (matches Tailwind max-w-*). Defaults to max-w-200. */
  maxWidth?: string;
}

export function PageHeader({
  title,
  description,
  children,
  className,
  maxWidth = "max-w-200",
}: PageHeaderProps) {
  const isMobile = useIsMobile();

  if (isMobile) {
    return (
      <div className={cn("w-full px-4 pb-4 pt-2", className)}>
        <h2 className="mb-3 text-balance text-foreground typo-h2">{title}</h2>
        {description ? (
          <p className="mb-3 text-muted-foreground typo-helper">{description}</p>
        ) : null}
        {children}
      </div>
    );
  }

  return (
    <div
      className={cn(
        "mx-auto w-full shrink-0 border-b border-border-subtle px-6 pb-4 pt-4 md:pt-6",
        maxWidth,
        className,
      )}
    >
      <h2 className="mb-1 text-balance text-foreground typo-h3">{title}</h2>
      {description ? <p className="mb-0 text-muted-foreground typo-helper">{description}</p> : null}
      {children}
    </div>
  );
}

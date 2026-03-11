import { useState, useEffect, useRef, type ReactNode } from "react";
import { cn } from "@/lib/utils/cn";

interface LargeTitleHeaderProps {
  /** Large title text */
  title: string;
  /** Optional subtitle below the large title */
  subtitle?: string;
  /** Additional content below the title (search bars, filters, etc.) */
  children?: ReactNode;
  /** Only apply the collapsing behavior on mobile */
  isMobile?: boolean;
}

/**
 * iOS 26 NavigationStack–style large-title header.
 *
 * On **mobile** this component MUST be placed **inside** a ScrollArea
 * (or any scrollable parent) so the IntersectionObserver sentinel can
 * actually leave the viewport. The compact inline title bar uses
 * `position: sticky` to pin at the top of the scroll container.
 *
 * On **desktop** it renders a standard static header — placement
 * doesn't matter.
 *
 * All visual properties reference CSS variables from the design system.
 */
export function LargeTitleHeader({
  title,
  subtitle,
  children,
  isMobile,
}: LargeTitleHeaderProps) {
  const [isCollapsed, setIsCollapsed] = useState(false);
  const sentinelRef = useRef<HTMLDivElement>(null);

  /* IntersectionObserver fires when the sentinel scrolls out of view */
  useEffect(() => {
    if (!isMobile || !sentinelRef.current) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        if (!entry) return;
        setIsCollapsed(!entry.isIntersecting);
      },
      { threshold: 0, rootMargin: "-1px 0px 0px 0px" },
    );

    observer.observe(sentinelRef.current);
    return () => observer.disconnect();
  }, [isMobile]);

  /* ── Desktop: simple static header ─────────────────────────────── */
  if (!isMobile) {
    return (
      <div className="pt-4 md:pt-6 pb-4 border-b border-border-subtle shrink-0 max-w-200 w-full mx-auto px-6">
        <h2 className="mb-1 text-balance text-foreground typo-h3">
          {title}
        </h2>
        {subtitle && (
          <p className="text-muted-foreground mt-1 typo-caption">
            {subtitle}
          </p>
        )}
        {children && <div className="mt-4">{children}</div>}
      </div>
    );
  }

  /* ── Mobile: collapsing large title (must be inside scroll area) ── */
  return (
    <>
      {/* Compact sticky title — pins to top of scroll viewport */}
      <div
        className={cn(
          "glass-nav-surface sticky top-0 z-10 flex items-center justify-center overflow-hidden transition-[height,opacity]",
          isCollapsed
            ? "h-11 border-b border-b-(--color-border-disabled) opacity-100"
            : "h-0 border-b-0 opacity-0",
        )}
      >
        <span className="text-foreground truncate px-4 typo-label">
          {title}
        </span>
      </div>

      {/* Sentinel — this 1px element triggers the observer */}
      <div
        ref={sentinelRef}
        className="h-px w-full shrink-0"
        aria-hidden="true"
      />

      {/* Large title area */}
      <div className="px-4 pt-2 pb-3 w-full">
        <h2 className="font-app text-foreground text-balance typo-h2">
          {title}
        </h2>
        {subtitle && (
          <p className="text-muted-foreground mt-1 typo-caption">
            {subtitle}
          </p>
        )}
      </div>

      {/* Additional header content (search, filters) */}
      {children && <div className="pb-4 w-full">{children}</div>}
    </>
  );
}

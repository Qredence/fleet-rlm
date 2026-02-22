import type { ComponentType } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/components/ui/utils";

// ── Types ───────────────────────────────────────────────────────────

interface SettingsNavItemProps {
  /** Lucide icon component. */
  icon: ComponentType<{ className?: string }>;
  /** Display label text. */
  label: string;
  /** Whether this nav item is currently active. */
  isActive: boolean;
  /** Click handler. */
  onClick: () => void;
  /** Mobile layout uses horizontal scrollable tabs with `touch-target`. */
  isMobile?: boolean;
  /** Additional class names. */
  className?: string;
}

// ── Component ───────────────────────────────────────────────────────

/**
 * SettingsNavItem -- a sidebar / tab-bar navigation button used in
 * SettingsDialog and SettingsPage.
 *
 * Typography is driven by the `data-slot="settings-nav-item-label"`
 * selector in `theme.css`, so the look can be re-themed from CSS alone.
 *
 * On mobile the item renders as a horizontal touch-target pill; on
 * desktop it renders as a left-aligned sidebar row.
 *
 * @example
 * ```tsx
 * <SettingsNavItem
 *   icon={Bell}
 *   label="Notifications"
 *   isActive={activeCategory === 'notifications'}
 *   onClick={() => setActiveCategory('notifications')}
 * />
 * ```
 */
function SettingsNavItem({
  icon: Icon,
  label,
  isActive,
  onClick,
  isMobile = false,
  className,
}: SettingsNavItemProps) {
  return (
    <Button
      data-slot="settings-nav-item"
      variant="ghost"
      className={cn(
        "rounded-lg h-auto",
        isMobile
          ? "gap-2 px-3 py-2.5 touch-target shrink-0"
          : "justify-start gap-2.5 px-3 py-2",
        isActive ? "bg-muted text-foreground" : "text-muted-foreground",
        className,
      )}
      onClick={onClick}
    >
      <Icon className="w-4 h-4 shrink-0" />
      <span data-slot="settings-nav-item-label">{label}</span>
    </Button>
  );
}

export { SettingsNavItem };
export type { SettingsNavItemProps };

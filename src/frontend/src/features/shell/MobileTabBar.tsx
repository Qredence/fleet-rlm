import { MessageSquare, HardDrive } from "lucide-react";
import type { NavItem } from "@/lib/data/types";
import { useNavigationStore } from "@/stores/navigationStore";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import { preloadNavRoute } from "@/lib/perf/routePreload";
import { isSectionSupported } from "@/lib/rlm-api";
import { cn } from "@/lib/utils/cn";

const tabs: { key: NavItem; label: string; icon: typeof MessageSquare }[] = [
  { key: "workspace", label: "Workspace", icon: MessageSquare },
  { key: "volumes", label: "Volumes", icon: HardDrive },
];

/**
 * iOS 26 Liquid Glass floating tab bar for the supported shell surfaces.
 *
 * All state consumed from NavigationStore — zero props.
 */
export function MobileTabBar() {
  const { activeNav } = useNavigationStore();
  const { navigateTo } = useAppNavigate();

  return (
    <div className="mobile-tab-bar-safe-inset pointer-events-none flex shrink-0 justify-center">
      <nav
        className="surface-glass-tab-bar pointer-events-auto relative overflow-hidden"
        role="tablist"
        aria-label="Main navigation"
      >
        {/* Glass highlight — top edge specular rim */}
        <div
          className="surface-glass-tab-highlight pointer-events-none absolute inset-x-0 top-0 h-px"
          aria-hidden="true"
        />

        <div className="flex items-stretch h-full px-2">
          {tabs.map((tab) => {
            const isActive = activeNav === tab.key;
            const isSupported = isSectionSupported(tab.key);
            const Icon = tab.icon;

            return (
              <button
                key={tab.key}
                role="tab"
                aria-selected={isActive}
                aria-label={tab.label}
                disabled={!isSupported}
                onClick={() => navigateTo(tab.key)}
                onPointerEnter={() => {
                  if (isSupported) void preloadNavRoute(tab.key);
                }}
                onFocus={() => {
                  if (isSupported) void preloadNavRoute(tab.key);
                }}
                className={cn(
                  "relative flex-1 flex flex-col items-center justify-center gap-0.5",
                  "touch-target min-w-13",
                  "transition-colors",
                  "font-app",
                  !isSupported && "opacity-50",
                )}
              >
                <Icon
                  className={cn(
                    "size-[22px] transition-colors",
                    isActive ? "text-accent" : "text-muted-foreground",
                  )}
                  strokeWidth={isActive ? 2.2 : 1.8}
                />
                <span
                  className={cn(
                    "font-app text-[length:var(--font-text-3xs-size)] leading-[var(--font-text-3xs-line-height)] tracking-[var(--font-text-3xs-tracking)] transition-colors",
                    isActive ? "font-medium" : "font-normal",
                    isActive ? "text-accent" : "text-muted-foreground",
                  )}
                >
                  {tab.label}
                </span>
              </button>
            );
          })}
        </div>
      </nav>
    </div>
  );
}

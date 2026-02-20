import {
  MessageSquare,
  Library,
  GitFork,
  Brain,
  BarChart3,
} from "lucide-react";
import type { NavItem } from "../data/types";
import { useNavigation } from "../hooks/useNavigation";
import { useAppNavigate } from "../hooks/useAppNavigate";
import { preloadNavRoute } from "../../lib/perf/routePreload";
import { toast } from "sonner";
import {
  BACKEND_CAPABILITY_TOAST,
  BACKEND_CAPABILITY_TOOLTIP,
  isSectionSupported,
} from "../../lib/rlm-api";
import { cn } from "./utils";

const tabs: { key: NavItem; label: string; icon: typeof MessageSquare }[] = [
  { key: "new", label: "Chat", icon: MessageSquare },
  { key: "skills", label: "Skills", icon: Library },
  { key: "taxonomy", label: "Taxonomy", icon: GitFork },
  { key: "memory", label: "Memory", icon: Brain },
  { key: "analytics", label: "Analytics", icon: BarChart3 },
];

/**
 * iOS 26 Liquid Glass floating tab bar.
 *
 * All state consumed from NavigationContext — zero props.
 */
export function MobileTabBar() {
  const { activeNav } = useNavigation();
  const { navigateTo } = useAppNavigate();

  return (
    <div
      className="shrink-0 flex justify-center pointer-events-none"
      style={{
        paddingBottom:
          "max(var(--glass-tab-bar-inset), env(safe-area-inset-bottom, 8px))",
        paddingLeft: "var(--glass-tab-bar-inset)",
        paddingRight: "var(--glass-tab-bar-inset)",
        paddingTop: "6px",
      }}
    >
      <nav
        className="pointer-events-auto relative overflow-hidden"
        style={{
          height: "var(--glass-tab-bar-height)",
          borderRadius: "var(--glass-tab-bar-radius)",
          backgroundColor: "var(--glass-tab-bg)",
          backdropFilter: "blur(var(--glass-tab-blur))",
          WebkitBackdropFilter: "blur(var(--glass-tab-blur))",
          boxShadow: "var(--glass-tab-shadow)",
          border: "0.5px solid var(--glass-tab-border)",
        }}
        role="tablist"
        aria-label="Main navigation"
      >
        {/* Glass highlight — top edge specular rim */}
        <div
          className="absolute inset-x-0 top-0 h-[1px] pointer-events-none"
          style={{
            background:
              "linear-gradient(90deg, transparent 8%, var(--glass-tab-highlight) 50%, transparent 92%)",
          }}
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
                aria-disabled={!isSupported}
                title={isSupported ? undefined : BACKEND_CAPABILITY_TOOLTIP}
                onClick={() => {
                  if (!isSupported) {
                    toast.info(BACKEND_CAPABILITY_TOAST);
                    return;
                  }
                  navigateTo(tab.key);
                }}
                onPointerEnter={() => {
                  if (isSupported) {
                    void preloadNavRoute(tab.key);
                  }
                }}
                onFocus={() => {
                  if (isSupported) {
                    void preloadNavRoute(tab.key);
                  }
                }}
                className={cn(
                  "relative flex-1 flex flex-col items-center justify-center gap-0.5",
                  "touch-target min-w-[52px]",
                  "transition-colors",
                  !isSupported && "opacity-50",
                )}
                style={{ fontFamily: "var(--font-family)" }}
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
                    "transition-colors",
                    isActive ? "text-accent" : "text-muted-foreground",
                  )}
                  style={{
                    fontFamily: "var(--font-family)",
                    fontSize: "var(--text-micro)",
                    fontWeight: isActive
                      ? "var(--font-weight-medium)"
                      : "var(--font-weight-regular)",
                    lineHeight: "1.2",
                  }}
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

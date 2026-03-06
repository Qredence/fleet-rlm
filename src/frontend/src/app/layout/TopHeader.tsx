import { LayoutGroup } from "motion/react";
import { PanelRight, SquarePen } from "lucide-react";
import type { NavItem } from "@/lib/data/types";
import { useNavigation } from "@/hooks/useNavigation";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import { useIsMobile } from "@/hooks/useIsMobile";
import { UserMenu } from "@/features/shell/UserMenu";
import { BrandMark } from "@/components/shared/BrandMark";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { IconButton } from "@/components/ui/icon-button";
import { NavTab } from "@/features/shell/NavTab";
import { cn } from "@/lib/utils/cn";
import { preloadNavRoute } from "@/lib/perf/routePreload";
import { isSectionSupported } from "@/lib/rlm-api";

// ── Tab definitions ─────────────────────────────────────────────────
const navItems: { key: NavItem; label: string }[] = [
  { key: "workspace", label: "RLM Workspace" },
  { key: "volumes", label: "Volumes" },
];

// ── Component ───────────────────────────────────────────────────────
/**
 * Top header bar.
 *
 * iOS 26 mobile: translucent Liquid Glass navigation bar with
 * backdrop-blur. Uses --glass-nav-* CSS variables so the user can
 * tweak the glass material from CSS alone.
 *
 * Desktop: solid background, inline nav tabs.
 *
 * All state consumed from NavigationContext — zero props.
 * User menu (avatar dropdown) replaces the standalone settings icon.
 */
export function TopHeader() {
  const { activeNav, isCanvasOpen, toggleCanvas, newSession } = useNavigation();
  const { navigateTo, navigate } = useAppNavigate();
  const isMobile = useIsMobile();

  return (
    <header
      className={cn(
        "flex items-center justify-between shrink-0",
        !isMobile && "border-b border-border-subtle bg-background",
      )}
      style={
        isMobile
          ? {
              padding: "10px 16px",
              paddingLeft: "max(16px, env(safe-area-inset-left, 16px))",
              paddingRight: "max(16px, env(safe-area-inset-right, 16px))",
              backgroundColor: "var(--glass-nav-bg)",
              backdropFilter: "blur(var(--glass-nav-blur))",
              WebkitBackdropFilter: "blur(var(--glass-nav-blur))",
              borderBottom: "0.5px solid var(--glass-nav-border)",
            }
          : {
              padding: "16px 32px",
            }
      }
    >
      {/* ── Logo ──────────────────────────────────────────────── */}
      <div className="flex items-center gap-2 shrink-0">
        <BrandMark className="h-[17px] w-[18px] shrink-0 text-foreground" />
      </div>

      {/* ── Navigation tabs (desktop only) ─────────────────────── */}
      {!isMobile && (
        <LayoutGroup id="headerNav">
          <nav className="flex items-center gap-2 overflow-x-auto no-scrollbar">
            {navItems.map((item) => {
              const isActive = activeNav === item.key;
              const isSupported = isSectionSupported(item.key);
              return (
                <NavTab
                  key={item.key}
                  onClick={() => navigateTo(item.key)}
                  onPointerEnter={() => {
                    if (isSupported) {
                      void preloadNavRoute(item.key);
                    }
                  }}
                  onFocus={() => {
                    if (isSupported) {
                      void preloadNavRoute(item.key);
                    }
                  }}
                  isActive={isActive}
                  label={item.label}
                  disabled={!isSupported}
                />
              );
            })}
          </nav>
        </LayoutGroup>
      )}

      {/* ── Action buttons ────────────────────────────────────── */}
      <div
        className={cn(
          "flex items-center shrink-0",
          isMobile ? "gap-1" : "gap-2",
        )}
      >
        {/* New Session */}
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="inline-flex">
              <IconButton
                onClick={() => {
                  newSession();
                  navigate("/app/workspace");
                }}
                aria-label="New Session"
                className={isMobile ? "touch-target" : undefined}
              >
                <SquarePen className="size-5 text-foreground" />
              </IconButton>
            </span>
          </TooltipTrigger>
          <TooltipContent side="bottom">New Session</TooltipContent>
        </Tooltip>

        {/* Side Panel */}
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="inline-flex">
              <IconButton
                onClick={toggleCanvas}
                isActive={isCanvasOpen}
                aria-label={
                  isCanvasOpen ? "Close side panel" : "Open side panel"
                }
                className={isMobile ? "touch-target" : undefined}
              >
                <PanelRight className="size-6" />
              </IconButton>
            </span>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            {isCanvasOpen ? "Close side panel" : "Open side panel"}
          </TooltipContent>
        </Tooltip>

        {/* User Menu (replaces standalone Settings gear icon) */}
        <UserMenu />
      </div>
    </header>
  );
}

import { LayoutGroup } from "motion/react";
import type { NavItem } from "@/lib/data/types";
import { useNavigation } from "@/hooks/useNavigation";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import { useIsMobile } from "@/components/ui/use-mobile";
import { UserMenu } from "@/features/UserMenu";
import { NotificationCenter } from "@/features/NotificationCenter";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { IconButton } from "@/components/ui/icon-button";
import { NavTab } from "@/components/ui/nav-tab";
import { cn } from "@/components/ui/utils";
import { preloadNavRoute } from "@/lib/perf/routePreload";
import {
  BACKEND_CAPABILITY_TOOLTIP,
  isSectionSupported,
} from "@/lib/rlm-api";
import headerSvg from "@/imports/svg-synwn0xtnf";

// ── Tab definitions ─────────────────────────────────────────────────
const navItems: { key: NavItem; label: string }[] = [
  { key: "new", label: "Chat" },
  { key: "skills", label: "Skills" },
  { key: "taxonomy", label: "Taxonomy" },
  { key: "memory", label: "Memory" },
  { key: "analytics", label: "Analytics" },
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
        <div className="w-[18px] h-[17px] shrink-0">
          <svg
            className="block size-full"
            fill="none"
            preserveAspectRatio="xMidYMid meet"
            viewBox="0 0 18 17"
          >
            <path
              clipRule="evenodd"
              d={headerSvg.p4dc2a80}
              fill="var(--foreground)"
              fillRule="evenodd"
            />
          </svg>
        </div>
      </div>

      {/* ── Navigation tabs (desktop only) ─────────────────────── */}
      {!isMobile && (
        <LayoutGroup id="headerNav">
          <nav className="flex items-center gap-2 overflow-x-auto no-scrollbar">
            {navItems.map((item) => {
              const isActive = activeNav === item.key;
              const isSupported = isSectionSupported(item.key);
              const tab = (
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
              if (isSupported) return tab;
              return (
                <Tooltip key={item.key}>
                  <TooltipTrigger asChild>
                    <span className="inline-flex">{tab}</span>
                  </TooltipTrigger>
                  <TooltipContent side="bottom">
                    {BACKEND_CAPABILITY_TOOLTIP}
                  </TooltipContent>
                </Tooltip>
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
                  navigate("/");
                }}
                aria-label="New Session"
                className={isMobile ? "touch-target" : undefined}
              >
                <svg className="size-5" fill="none" viewBox="0 0 20 20">
                  <path d={headerSvg.p2fd67200} fill="var(--foreground)" />
                </svg>
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
                <svg className="size-6" fill="none" viewBox="0 0 24 24">
                  <path d={headerSvg.p3a006380} fill="currentColor" />
                  <path d={headerSvg.p20a25d00} fill="currentColor" />
                  <path d={headerSvg.p1337c9c0} fill="currentColor" />
                  <path d={headerSvg.pe8ed000} fill="currentColor" />
                  <path d={headerSvg.p2032bd00} fill="currentColor" />
                </svg>
              </IconButton>
            </span>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            {isCanvasOpen ? "Close side panel" : "Open side panel"}
          </TooltipContent>
        </Tooltip>

        {/* Notification Center */}
        <NotificationCenter />

        {/* User Menu (replaces standalone Settings gear icon) */}
        <UserMenu />
      </div>
    </header>
  );
}

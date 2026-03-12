import { LayoutGroup } from "motion/react";
import { PanelRight, SquarePen } from "lucide-react";
import type { NavItem } from "@/lib/data/types";
import { useNavigationStore } from "@/stores/navigationStore";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import { useIsMobile } from "@/hooks/useIsMobile";
import { UserMenu } from "@/features/shell/UserMenu";
import { BrandMark } from "@/components/shared/BrandMark";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { Button } from "@/components/ui/button";
import { IconButton } from "@/components/ui/icon-button";
import { NavTab } from "@/components/shared/NavTab";
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
  const { activeNav, isCanvasOpen, toggleCanvas, newSession } = useNavigationStore();
  const { navigateTo, navigate } = useAppNavigate();
  const isMobile = useIsMobile();
  const panelLabel =
    activeNav === "workspace" ? "message inspector" : "side panel";

  const handleNewSession = () => {
    newSession();
    navigate("/app/workspace");
  };

  const SidePanelToggle = (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex">
          <IconButton
            onClick={toggleCanvas}
            isActive={isCanvasOpen}
            aria-label={
              isCanvasOpen ? `Close ${panelLabel}` : `Open ${panelLabel}`
            }
            className={
              isMobile
                ? "touch-target"
                : "h-8 w-8 rounded-lg hover:bg-background/80"
            }
          >
            <PanelRight className={isMobile ? "size-6" : "size-5"} />
          </IconButton>
        </span>
      </TooltipTrigger>
      <TooltipContent side="bottom">
        {isCanvasOpen ? `Close ${panelLabel}` : `Open ${panelLabel}`}
      </TooltipContent>
    </Tooltip>
  );

  return (
    <header
      className={cn(
        "flex shrink-0 items-center justify-between gap-4",
        !isMobile && "border-b border-border-subtle/80 bg-background/95",
      )}
      style={
        isMobile
          ? {
              paddingBlock: "var(--header-padding-block-mobile)",
              paddingLeft:
                "max(var(--header-padding-inline-mobile), var(--safe-area-inset-left))",
              paddingRight:
                "max(var(--header-padding-inline-mobile), var(--safe-area-inset-right))",
              backgroundColor: "var(--glass-nav-bg)",
              backdropFilter: "blur(var(--glass-nav-blur))",
              WebkitBackdropFilter: "blur(var(--glass-nav-blur))",
              borderBottom: "0.5px solid var(--glass-nav-border)",
            }
          : {
              paddingBlock: "var(--header-padding-block-desktop)",
              paddingInline: "var(--header-padding-inline-desktop)",
            }
      }
    >
      {/* ── Logo ──────────────────────────────────────────────── */}
      <div className="flex items-center gap-2 shrink-0">
        <BrandMark className="h-4.25 w-4.5 shrink-0 text-foreground" />
      </div>

      {/* ── Navigation tabs (desktop only) ─────────────────────── */}
      {!isMobile && (
        <LayoutGroup id="headerNav">
          <nav className="flex items-center gap-1 overflow-x-auto rounded-xl border border-border-subtle/70 bg-muted/35 p-1 no-scrollbar">
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
          isMobile ? "gap-1" : "gap-2.5",
        )}
      >
        {isMobile ? (
          <>
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="inline-flex">
                  <IconButton
                    onClick={handleNewSession}
                    aria-label="New Session"
                    className="touch-target"
                  >
                    <SquarePen className="size-5" />
                  </IconButton>
                </span>
              </TooltipTrigger>
              <TooltipContent side="bottom">New Session</TooltipContent>
            </Tooltip>

            {SidePanelToggle}
          </>
        ) : (
          <div className="flex items-center gap-1 rounded-xl border border-border-subtle/70 bg-muted/35 p-1">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-8 rounded-lg px-3 text-xs font-medium text-foreground hover:bg-background/80"
              onClick={handleNewSession}
            >
              <SquarePen className="size-4" />
              <span>New Session</span>
            </Button>

            <div className="h-5 w-px bg-border-subtle/80" aria-hidden="true" />

            {SidePanelToggle}
          </div>
        )}

        {/* User Menu (replaces standalone Settings gear icon) */}
        <UserMenu />
      </div>
    </header>
  );
}

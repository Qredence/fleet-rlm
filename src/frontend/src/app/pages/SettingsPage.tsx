/**
 * Full-page settings view at `/settings`.
 *
 * Renders the same settings pane system as the SettingsDialog but in a
 * page layout within the app shell. Accessible via direct URL navigation,
 * ⌘K command palette, or user menu.
 */
import { useState } from "react";
import { useNavigate } from "react-router";
import { ArrowLeft } from "lucide-react";
import { typo } from "../components/config/typo";
import { useNavigation } from "../components/hooks/useNavigation";
import { useIsMobile } from "../components/ui/use-mobile";
import { IconButton } from "../components/ui/icon-button";
import { ScrollArea } from "../components/ui/scroll-area";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "../components/ui/tooltip";
import { cn } from "../components/ui/utils";

import {
  categories,
  type Category,
} from "../components/features/settings/types";
import { SettingsNavItem } from "../components/shared/SettingsNavItem";
import { SettingsPaneContent } from "../components/features/settings/SettingsPaneContent";

// ── Component ───────────────────────────────────────────────────────
export function SettingsPage() {
  const { isDark, toggleTheme } = useNavigation();
  const isMobile = useIsMobile();
  const navigate = useNavigate();
  const [activeCategory, setActiveCategory] = useState<Category>("general");

  const activeCategoryLabel =
    categories.find((c) => c.key === activeCategory)?.label ?? "";

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div
        className={cn(
          "flex items-center gap-3 shrink-0 border-b border-border-subtle",
          isMobile ? "px-4 py-3" : "px-6 py-4",
        )}
      >
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="inline-flex">
              <IconButton
                onClick={() => navigate(-1)}
                aria-label="Go back"
                className={isMobile ? "touch-target" : undefined}
              >
                <ArrowLeft className="size-5 text-muted-foreground" />
              </IconButton>
            </span>
          </TooltipTrigger>
          <TooltipContent side="bottom">Go back</TooltipContent>
        </Tooltip>
        <h1 className="text-foreground" style={typo.h3}>
          Settings
        </h1>
      </div>

      {isMobile ? (
        <div className="flex flex-col flex-1 min-h-0">
          <div className="shrink-0 border-b border-border-subtle">
            <nav className="flex gap-0.5 px-3 py-2 overflow-x-auto no-scrollbar">
              {categories.map((cat) => (
                <SettingsNavItem
                  key={cat.key}
                  icon={cat.icon}
                  label={cat.label}
                  isActive={activeCategory === cat.key}
                  onClick={() => setActiveCategory(cat.key)}
                  isMobile
                />
              ))}
            </nav>
          </div>
          <ScrollArea className="flex-1">
            <div className="px-4">
              <SettingsPaneContent
                activeCategory={activeCategory}
                isDark={isDark}
                onToggleTheme={toggleTheme}
              />
            </div>
          </ScrollArea>
        </div>
      ) : (
        <div className="flex flex-1 min-h-0">
          <div className="w-[200px] shrink-0 border-r border-border-subtle bg-secondary/30 flex flex-col">
            <nav className="flex flex-col gap-0.5 px-2 py-3 flex-1">
              {categories.map((cat) => (
                <SettingsNavItem
                  key={cat.key}
                  icon={cat.icon}
                  label={cat.label}
                  isActive={activeCategory === cat.key}
                  onClick={() => setActiveCategory(cat.key)}
                />
              ))}
            </nav>
          </div>
          <div className="flex-1 flex flex-col min-w-0">
            <div className="px-6 pt-5 pb-3 border-b border-border-subtle">
              <span className="text-foreground" style={typo.h4}>
                {activeCategoryLabel}
              </span>
            </div>
            <ScrollArea className="flex-1">
              <div className="px-6">
                <SettingsPaneContent
                  activeCategory={activeCategory}
                  isDark={isDark}
                  onToggleTheme={toggleTheme}
                />
              </div>
            </ScrollArea>
          </div>
        </div>
      )}
    </div>
  );
}

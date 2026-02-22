/**
 * Full-page settings view at `/settings`.
 *
 * Renders the same settings pane system as the SettingsDialog but in a
 * page layout within the app shell. Accessible via direct URL navigation,
 * ⌘K command palette, or user menu.
 */
import { useNavigate } from "react-router";
import { ArrowLeft } from "lucide-react";
import { typo } from "@/lib/config/typo";
import { useNavigation } from "@/hooks/useNavigation";
import { useIsMobile } from "@/components/ui/use-mobile";
import { IconButton } from "@/components/ui/icon-button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { cn } from "@/components/ui/utils";

import { SettingsPaneContent } from "@/features/settings/SettingsPaneContent";

// ── Component ───────────────────────────────────────────────────────
export function SettingsPage() {
  const { isDark, toggleTheme } = useNavigation();
  const isMobile = useIsMobile();
  const navigate = useNavigate();

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

      <div className="flex flex-col flex-1 min-h-0">
        <div
          className={cn(
            "shrink-0 border-b border-border-subtle",
            isMobile ? "px-4 py-3" : "px-6 py-4",
          )}
        >
          <span className="text-foreground" style={typo.h4}>
            General
          </span>
          <p className="mt-1 text-sm text-muted-foreground">
            Functional settings only for v0.4.8: theme, anonymous telemetry, and
            LiteLLM runtime integration.
          </p>
        </div>
        <ScrollArea className="flex-1">
          <div className={cn(isMobile ? "px-4" : "px-6")}>
            <SettingsPaneContent isDark={isDark} onToggleTheme={toggleTheme} />
          </div>
        </ScrollArea>
      </div>
    </div>
  );
}

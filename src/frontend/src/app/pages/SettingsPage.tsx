/**
 * Full-page settings view at `/settings`.
 *
 * Renders the settings pane system in a page layout within the app shell.
 * Accessible via direct URL navigation, ⌘K command palette, or user menu.
 */
import { useSearch, useRouter } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { useThemeStore } from "@/stores/themeStore";
import { useIsMobile } from "@/hooks/useIsMobile";
import { IconButton } from "@/components/ui/icon-button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils/cn";

import { GroupedSettingsPane } from "@/features/settings/GroupedSettingsPane";
import {
  sectionDescriptions,
  settingsSections,
  type SettingsSection,
} from "@/features/settings/types";

// ── Component ───────────────────────────────────────────────────────
export function SettingsPage() {
  const { isDark, toggle: toggleTheme } = useThemeStore();
  const isMobile = useIsMobile();
  const router = useRouter();
  const searchParams = useSearch({ strict: false }) as { section?: string };

  const sectionFromQuery = searchParams.section;
  const selectedSection =
    sectionFromQuery &&
    settingsSections.some((section) => section.key === sectionFromQuery)
      ? (sectionFromQuery as SettingsSection)
      : undefined;

  const sectionTitle =
    settingsSections.find((section) => section.key === selectedSection)
      ?.label ?? "Settings";

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
                onClick={() => router.history.back()}
                aria-label="Go back"
                className={isMobile ? "touch-target" : undefined}
              >
                <ArrowLeft className="size-5 text-muted-foreground" />
              </IconButton>
            </span>
          </TooltipTrigger>
          <TooltipContent side="bottom">Go back</TooltipContent>
        </Tooltip>
        <h1 className="text-foreground typo-h3">Settings</h1>
      </div>

      <div className="flex flex-col flex-1 min-h-0">
        <div
          className={cn(
            "shrink-0 border-b border-border-subtle",
            isMobile ? "px-4 py-3" : "px-6 py-4",
          )}
        >
          <span className="text-foreground typo-h4">{sectionTitle}</span>
          <p className="mt-1 text-sm text-muted-foreground">
            {selectedSection
              ? sectionDescriptions[selectedSection]
              : "Configure theme, telemetry, LM integration, and runtime connectivity."}
          </p>
        </div>
        <ScrollArea className="flex-1">
          <div className={cn(isMobile ? "px-4" : "px-6")}>
            <GroupedSettingsPane
              isDark={isDark}
              onToggleTheme={toggleTheme}
              section={selectedSection}
            />
          </div>
        </ScrollArea>
      </div>
    </div>
  );
}

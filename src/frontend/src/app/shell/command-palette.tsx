/**
 * Command Palette — global keyboard-driven quick actions.
 *
 * Mirrors the current product shell: Workbench, Volumes, and Settings.
 */
import { useEffect, useState, useCallback, useRef } from "react";
import {
  Search,
  Zap,
  HardDrive,
  Plus,
  Moon,
  Sun,
  Settings,
} from "lucide-react";
import { Command } from "cmdk";
import { useTelemetry } from "@/lib/telemetry/useTelemetry";
import type { NavItem } from "@/stores/navigation-types";
import { requestSettingsDialogOpen } from "@/screens/settings/settings-events";
import { useWorkspaceShellActions } from "@/screens/workspace/workspace-shell-contract";
import { useThemeStore } from "@/stores/themeStore";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import { cn } from "@/lib/utils";

interface PageItem {
  key: NavItem;
  label: string;
  icon: typeof Zap;
}

const pages: PageItem[] = [
  { key: "workspace", label: "Workbench", icon: Zap },
  { key: "volumes", label: "Volumes", icon: HardDrive },
  { key: "settings", label: "Settings", icon: Settings },
];

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const { newSession } = useWorkspaceShellActions();
  const { isDark, toggle: toggleTheme } = useThemeStore();
  const { navigateTo } = useAppNavigate();
  const telemetry = useTelemetry();

  const [search, setSearch] = useState("");
  const prevOpenRef = useRef(open);

  useEffect(() => {
    if (open && !prevOpenRef.current) {
      telemetry.capture("command_palette_opened", {
        trigger: "keyboard_shortcut",
      });
    }
    prevOpenRef.current = open;
  }, [open, telemetry]);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        onOpenChange(!open);
      }
      if (e.key === "Escape" && open) {
        onOpenChange(false);
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, onOpenChange]);

  const close = useCallback(() => {
    onOpenChange(false);
    setSearch("");
  }, [onOpenChange]);

  const openSettings = useCallback(() => {
    const wasHandledByDialog = requestSettingsDialogOpen();
    if (!wasHandledByDialog) {
      navigateTo("settings");
    }
    close();
  }, [close, navigateTo]);

  const navigateToPage = useCallback(
    (nav: NavItem) => {
      telemetry.capture("command_palette_action_selected", {
        action_type: "page",
        action_value: nav,
      });
      if (nav === "settings") {
        openSettings();
        return;
      }
      navigateTo(nav);
      close();
    },
    [navigateTo, close, openSettings, telemetry],
  );

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-100">
      <div
        className="surface-glass-overlay absolute inset-0"
        onClick={close}
        aria-hidden="true"
      />

      <div
        className="absolute inset-0 flex items-start justify-center pt-[min(20vh,120px)] px-4"
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        onClick={close}
      >
        <div
          className="rounded-card-token w-full max-w-140 overflow-hidden border-subtle bg-popover shadow-(--shadow-200-stronger)"
          onClick={(e) => e.stopPropagation()}
        >
          <Command
            loop
            shouldFilter
            className="flex flex-col"
            onKeyDown={(e: React.KeyboardEvent) => {
              if (e.key === "Escape") close();
            }}
          >
            <div className="flex items-center gap-2 px-4 border-b border-border-subtle focus-within:ring-2 focus-within:ring-inset focus-within:ring-ring/50">
              <Search
                className="size-4 text-muted-foreground shrink-0"
                aria-hidden="true"
              />
              <Command.Input
                value={search}
                onValueChange={setSearch}
                placeholder="Search pages and actions..."
                className="flex-1 h-12 bg-transparent text-foreground placeholder:text-muted-foreground border-0 focus-visible:outline-none typo-label"
                autoFocus
              />
              <kbd className="hidden sm:inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-muted border-subtle text-muted-foreground typo-micro">
                ESC
              </kbd>
            </div>

            <Command.List className="max-h-90 overflow-y-auto overscroll-contain p-2">
              <Command.Empty className="py-8 text-center text-muted-foreground typo-caption">
                No results found.
              </Command.Empty>

              <Command.Group
                heading={
                  <span className="text-muted-foreground px-2 pb-1 typo-micro">
                    Pages
                  </span>
                }
              >
                {pages.map((page) => {
                  const Icon = page.icon;
                  return (
                    <Command.Item
                      key={page.key}
                      value={`page ${page.label}`}
                      onSelect={() => navigateToPage(page.key)}
                      className={cn(
                        "flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer transition-colors min-h-10",
                        "text-foreground data-[selected=true]:bg-muted",
                      )}
                    >
                      <Icon
                        className="size-5 text-muted-foreground shrink-0"
                        aria-hidden="true"
                        strokeWidth={1.5}
                      />
                      <span className="typo-label">{page.label}</span>
                    </Command.Item>
                  );
                })}
              </Command.Group>

              <Command.Group
                heading={
                  <span className="text-muted-foreground px-2 pb-1 typo-micro">
                    Actions
                  </span>
                }
              >
                <Command.Item
                  value="action new session new chat"
                  onSelect={() => {
                    telemetry.capture("command_palette_action_selected", {
                      action_type: "action",
                      action_value: "new_session",
                    });
                    newSession();
                    navigateTo("workspace");
                    close();
                  }}
                  className={cn(
                    "flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer transition-colors min-h-10",
                    "text-foreground data-[selected=true]:bg-muted",
                  )}
                >
                  <Plus
                    className="size-5 text-muted-foreground shrink-0"
                    aria-hidden="true"
                    strokeWidth={1.5}
                  />
                  <span className="typo-label">New Session</span>
                </Command.Item>

                <Command.Item
                  value={`action toggle theme ${isDark ? "light" : "dark"} mode`}
                  onSelect={() => {
                    telemetry.capture("command_palette_action_selected", {
                      action_type: "action",
                      action_value: "toggle_theme",
                      new_theme: isDark ? "light" : "dark",
                    });
                    toggleTheme();
                    close();
                  }}
                  className={cn(
                    "flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer transition-colors min-h-10",
                    "text-foreground data-[selected=true]:bg-muted",
                  )}
                >
                  {isDark ? (
                    <Sun
                      className="size-5 text-muted-foreground shrink-0"
                      aria-hidden="true"
                      strokeWidth={1.5}
                    />
                  ) : (
                    <Moon
                      className="size-5 text-muted-foreground shrink-0"
                      aria-hidden="true"
                      strokeWidth={1.5}
                    />
                  )}
                  <span className="typo-label">
                    Switch to {isDark ? "Light" : "Dark"} Mode
                  </span>
                </Command.Item>

                <Command.Item
                  value="action open settings preferences"
                  onSelect={() => {
                    telemetry.capture("command_palette_action_selected", {
                      action_type: "action",
                      action_value: "open_settings",
                    });
                    openSettings();
                  }}
                  className={cn(
                    "flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer transition-colors min-h-10",
                    "text-foreground data-[selected=true]:bg-muted",
                  )}
                >
                  <Settings
                    className="size-5 text-muted-foreground shrink-0"
                    aria-hidden="true"
                    strokeWidth={1.5}
                  />
                  <span className="typo-label">Open Settings</span>
                </Command.Item>
              </Command.Group>
            </Command.List>

            <div className="flex items-center gap-3 px-4 py-2.5 border-t border-border-subtle">
              <span className="text-muted-foreground typo-micro">
                Navigate with
              </span>
              <div className="flex items-center gap-1">
                <kbd className="inline-flex items-center px-1.5 py-0.5 rounded bg-muted border-subtle text-muted-foreground typo-micro">
                  &uarr;
                </kbd>
                <kbd className="inline-flex items-center px-1.5 py-0.5 rounded bg-muted border-subtle text-muted-foreground typo-micro">
                  &darr;
                </kbd>
              </div>
              <span className="text-muted-foreground typo-micro">
                to select
              </span>
              <kbd className="inline-flex items-center px-1.5 py-0.5 rounded bg-muted border-subtle text-muted-foreground typo-micro">
                &crarr;
              </kbd>
            </div>
          </Command>
        </div>
      </div>
    </div>
  );
}

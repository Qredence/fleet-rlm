/**
 * Command Palette — global keyboard-driven quick actions.
 *
 * Mirrors the current product shell: Workbench, Volumes, Optimization, and Settings.
 */
import { useEffect, useState, useCallback, useRef } from "react";
import { Zap, HardDrive, Plus, Moon, Sun, Settings, Sparkles } from "lucide-react";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useTelemetry } from "@/lib/telemetry/use-telemetry";
import type { NavItem } from "@/stores/navigation-types";
import { requestSettingsDialogOpen } from "@/screens/settings/settings-events";
import { useWorkspaceShellActions } from "@/screens/workspace/workspace-shell-contract";
import { useThemeStore } from "@/stores/theme-store";
import { useAppNavigate } from "@/hooks/use-app-navigate";
import { cn } from "@/lib/utils";

interface PageItem {
  key: NavItem;
  label: string;
  icon: typeof Zap;
}

const pages: PageItem[] = [
  { key: "workspace", label: "Workbench", icon: Zap },
  { key: "volumes", label: "Volumes", icon: HardDrive },
  { key: "optimization", label: "Optimization", icon: Sparkles },
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

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        onOpenChange(nextOpen);
        if (!nextOpen) {
          setSearch("");
        }
      }}
    >
      <DialogContent className="rounded-card-token top-[min(20vh,120px)] translate-y-0 w-full max-w-140 overflow-hidden border-subtle bg-popover p-0 shadow-(--shadow-200-stronger) [&>button:last-child]:hidden">
        <DialogHeader className="sr-only">
          <DialogTitle>Command palette</DialogTitle>
          <DialogDescription>Search pages and run quick actions.</DialogDescription>
        </DialogHeader>
        <div onClick={(event) => event.stopPropagation()}>
          <Command
            loop
            shouldFilter
            className="flex flex-col"
            onKeyDown={(e: React.KeyboardEvent) => {
              if (e.key === "Escape") close();
            }}
          >
            <div className="flex items-center gap-2 border-b border-border-subtle pr-4">
              <CommandInput
                autoFocus
                className="h-12 flex-1 border-0 bg-transparent px-4 text-foreground placeholder:text-muted-foreground focus-visible:outline-none"
                onValueChange={setSearch}
                placeholder="Search pages and actions..."
                value={search}
              />
              <kbd className="hidden rounded bg-muted px-1.5 py-0.5 text-muted-foreground typo-micro sm:inline-flex sm:items-center sm:gap-0.5">
                ESC
              </kbd>
            </div>

            <CommandList className="max-h-90 overflow-y-auto overscroll-contain p-2">
              <CommandEmpty className="py-8 text-center text-muted-foreground typo-caption">
                No results found.
              </CommandEmpty>

              <CommandGroup
                heading={<span className="text-muted-foreground px-2 pb-1 typo-micro">Pages</span>}
              >
                {pages.map((page) => {
                  const Icon = page.icon;
                  return (
                    <CommandItem
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
                    </CommandItem>
                  );
                })}
              </CommandGroup>

              <CommandGroup
                heading={
                  <span className="text-muted-foreground px-2 pb-1 typo-micro">Actions</span>
                }
              >
                <CommandItem
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
                </CommandItem>

                <CommandItem
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
                  <span className="typo-label">Switch to {isDark ? "Light" : "Dark"} Mode</span>
                </CommandItem>

                <CommandItem
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
                </CommandItem>
              </CommandGroup>
            </CommandList>

            <div className="flex items-center gap-3 border-t border-border-subtle px-4 py-2.5">
              <span className="text-muted-foreground typo-micro">Navigate with</span>
              <div className="flex items-center gap-1">
                <kbd className="inline-flex items-center px-1.5 py-0.5 rounded bg-muted border-subtle text-muted-foreground typo-micro">
                  &uarr;
                </kbd>
                <kbd className="inline-flex items-center px-1.5 py-0.5 rounded bg-muted border-subtle text-muted-foreground typo-micro">
                  &darr;
                </kbd>
              </div>
              <span className="text-muted-foreground typo-micro">to select</span>
              <kbd className="inline-flex items-center px-1.5 py-0.5 rounded bg-muted border-subtle text-muted-foreground typo-micro">
                &crarr;
              </kbd>
            </div>
          </Command>
        </div>
      </DialogContent>
    </Dialog>
  );
}

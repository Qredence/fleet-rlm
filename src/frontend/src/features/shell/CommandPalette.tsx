/**
 * Command Palette — global keyboard-driven quick actions.
 *
 * In FastAPI-only mode, unsupported sections remain visible but disabled.
 */
import { useEffect, useState, useCallback, useRef } from "react";
import {
  Search,
  Zap,
  BarChart3,
  HardDrive,
  Brain,
  Plus,
  Moon,
  Sun,
  Settings,
  Layers,
} from "lucide-react";
import { Command } from "cmdk";
import { useTelemetry } from "@/lib/telemetry/useTelemetry";
import { toast } from "sonner";
import { typo } from "@/lib/config/typo";
import type { NavItem } from "@/lib/data/types";
import { useNavigation } from "@/hooks/useNavigation";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import {
  BACKEND_CAPABILITY_TOAST,
  BACKEND_CAPABILITY_TOOLTIP,
  isSectionSupported,
} from "@/lib/rlm-api";
import { cn } from "@/lib/utils/cn";

interface PageItem {
  key: NavItem;
  label: string;
  icon: typeof Zap;
}

const pages: PageItem[] = [
  { key: "new", label: "Chat", icon: Zap },
  { key: "skills", label: "Skill Library", icon: Layers },
  { key: "taxonomy", label: "Volume Browser", icon: HardDrive },
  { key: "memory", label: "Memory", icon: Brain },
  { key: "analytics", label: "Analytics Dashboard", icon: BarChart3 },
];

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const { newSession, isDark, toggleTheme } = useNavigation();
  const { navigateTo, navigate } = useAppNavigate();
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

  const navigateToPage = useCallback(
    (nav: NavItem) => {
      if (!isSectionSupported(nav)) {
        toast.info(BACKEND_CAPABILITY_TOAST);
        return;
      }
      telemetry.capture("command_palette_action_selected", {
        action_type: "page",
        action_value: nav,
      });
      navigateTo(nav);
      close();
    },
    [navigateTo, close, telemetry],
  );

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-100">
      <div
        className="absolute inset-0"
        style={{ backgroundColor: "var(--glass-overlay)" }}
        onClick={close}
        aria-hidden="true"
      />

      <div
        className="absolute inset-0 flex items-start justify-center pt-[min(20vh,120px)] px-4"
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
      >
        <div
          className="w-full max-w-140 overflow-hidden border border-border-subtle"
          style={{
            borderRadius: "var(--radius-card)",
            backgroundColor: "var(--popover)",
            boxShadow: "var(--shadow-200-stronger)",
          }}
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
                className="flex-1 h-12 bg-transparent text-foreground placeholder:text-muted-foreground border-0 focus-visible:outline-none"
                style={typo.label}
                autoFocus
              />
              <kbd
                className="hidden sm:inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-muted border border-border-subtle text-muted-foreground"
                style={typo.micro}
              >
                ESC
              </kbd>
            </div>

            <Command.List
              className="max-h-90 overflow-y-auto p-2"
              style={{ overscrollBehavior: "contain" }}
            >
              <Command.Empty
                className="py-8 text-center text-muted-foreground"
                style={typo.caption}
              >
                No results found.
              </Command.Empty>

              <Command.Group
                heading={
                  <span
                    className="text-muted-foreground px-2 pb-1"
                    style={typo.micro}
                  >
                    Pages
                  </span>
                }
              >
                {pages.map((page) => {
                  const Icon = page.icon;
                  const supported = isSectionSupported(page.key);
                  return (
                    <Command.Item
                      key={page.key}
                      value={`page ${page.label}`}
                      title={supported ? undefined : BACKEND_CAPABILITY_TOOLTIP}
                      onSelect={() => navigateToPage(page.key)}
                      className={cn(
                        "flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer transition-colors min-h-10",
                        "text-foreground data-[selected=true]:bg-muted",
                        !supported && "opacity-50",
                      )}
                    >
                      <Icon
                        className="size-4 text-muted-foreground shrink-0"
                        aria-hidden="true"
                      />
                      <span style={typo.label}>{page.label}</span>
                    </Command.Item>
                  );
                })}
              </Command.Group>

              <Command.Group
                heading={
                  <span
                    className="text-muted-foreground px-2 pb-1"
                    style={typo.micro}
                  >
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
                    navigate("/");
                    close();
                  }}
                  className={cn(
                    "flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer transition-colors min-h-10",
                    "text-foreground data-[selected=true]:bg-muted",
                  )}
                >
                  <Plus
                    className="size-4 text-muted-foreground shrink-0"
                    aria-hidden="true"
                  />
                  <span style={typo.label}>New Session</span>
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
                      className="size-4 text-muted-foreground shrink-0"
                      aria-hidden="true"
                    />
                  ) : (
                    <Moon
                      className="size-4 text-muted-foreground shrink-0"
                      aria-hidden="true"
                    />
                  )}
                  <span style={typo.label}>
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
                    navigate("/settings");
                    close();
                  }}
                  className={cn(
                    "flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer transition-colors min-h-10",
                    "text-foreground data-[selected=true]:bg-muted",
                  )}
                >
                  <Settings
                    className="size-4 text-muted-foreground shrink-0"
                    aria-hidden="true"
                  />
                  <span style={typo.label}>Open Settings</span>
                </Command.Item>
              </Command.Group>
            </Command.List>

            <div className="flex items-center gap-3 px-4 py-2.5 border-t border-border-subtle">
              <span className="text-muted-foreground" style={typo.micro}>
                Navigate with
              </span>
              <div className="flex items-center gap-1">
                <kbd
                  className="inline-flex items-center px-1.5 py-0.5 rounded bg-muted border border-border-subtle text-muted-foreground"
                  style={typo.micro}
                >
                  &uarr;
                </kbd>
                <kbd
                  className="inline-flex items-center px-1.5 py-0.5 rounded bg-muted border border-border-subtle text-muted-foreground"
                  style={typo.micro}
                >
                  &darr;
                </kbd>
              </div>
              <span className="text-muted-foreground" style={typo.micro}>
                to select
              </span>
              <kbd
                className="inline-flex items-center px-1.5 py-0.5 rounded bg-muted border border-border-subtle text-muted-foreground"
                style={typo.micro}
              >
                &crarr;
              </kbd>
            </div>
          </Command>
        </div>
      </div>
    </div>
  );
}

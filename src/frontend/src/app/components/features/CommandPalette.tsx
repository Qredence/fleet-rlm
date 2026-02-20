/**
 * Command Palette — global ⌘K search across skills, pages, memories, and actions.
 *
 * Built on the `cmdk` package (already installed). Triggered by:
 *   - ⌘K / Ctrl+K keyboard shortcut
 *   - Exposed `open` / `onOpenChange` props for external triggers
 *
 * Searches:
 *   - Skills (name, domain, tags)
 *   - Memories (content, source, tags, type)
 *   - Pages (navigation tabs)
 *   - Actions (new session, toggle theme, open settings, etc.)
 */
import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import {
  Search,
  Zap,
  BarChart3,
  GitFork,
  Brain,
  Plus,
  Moon,
  Sun,
  Settings,
  FileText,
  Layers,
  Lightbulb,
  Compass,
  MessageSquare,
  BookOpen,
  Pin,
} from "lucide-react";
import { Command } from "cmdk";
import { usePostHog } from "@posthog/react";
import { typo } from "../config/typo";
import { useSkills } from "../hooks/useSkills";
import { useMemory } from "../hooks/useMemory";
import type { NavItem, MemoryType } from "../data/types";
import { useNavigation } from "../hooks/useNavigation";
import { useAppNavigate } from "../hooks/useAppNavigate";
import { cn } from "../ui/utils";

// ── Page items ──────────────────────────────────────────────────────

interface PageItem {
  key: NavItem;
  label: string;
  icon: typeof Zap;
}

const pages: PageItem[] = [
  { key: "new", label: "Chat", icon: Zap },
  { key: "skills", label: "Skill Library", icon: Layers },
  { key: "taxonomy", label: "Taxonomy Browser", icon: GitFork },
  { key: "memory", label: "Memory", icon: Brain },
  { key: "analytics", label: "Analytics Dashboard", icon: BarChart3 },
];

// ── Memory type icon map ────────────────────────────────────────────

const MEMORY_TYPE_ICON: Record<MemoryType, typeof Brain> = {
  fact: Lightbulb,
  preference: Compass,
  session: MessageSquare,
  knowledge: BookOpen,
  directive: Brain,
};

const MEMORY_TYPE_LABEL: Record<MemoryType, string> = {
  fact: "Fact",
  preference: "Preference",
  session: "Session",
  knowledge: "Knowledge",
  directive: "Directive",
};

// ── Component ───────────────────────────────────────────────────────

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const { newSession, isDark, toggleTheme } = useNavigation();
  const { navigateTo, navigateToSkill, navigate } = useAppNavigate();
  const { skills: allSkills } = useSkills();
  const { entries: allMemories } = useMemory();
  const posthog = usePostHog();

  const [search, setSearch] = useState("");
  const prevOpenRef = useRef(open);

  // PostHog: Capture command palette opened event
  useEffect(() => {
    if (open && !prevOpenRef.current) {
      posthog?.capture("command_palette_opened", {
        trigger: "keyboard_shortcut",
      });
    }
    prevOpenRef.current = open;
  }, [open, posthog]);

  // ── Keyboard shortcut ───────────────────────────────────────────
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

  // ── Handlers ────────────────────────────────────────────────────
  const close = useCallback(() => {
    onOpenChange(false);
    setSearch("");
  }, [onOpenChange]);

  const navigateToPage = useCallback(
    (nav: NavItem) => {
      // PostHog: Capture command palette action selection
      posthog?.capture("command_palette_action_selected", {
        action_type: "page",
        action_value: nav,
      });
      navigateTo(nav);
      close();
    },
    [navigateTo, close, posthog],
  );

  const openSkill = useCallback(
    (skillId: string) => {
      // PostHog: Capture command palette action selection
      posthog?.capture("command_palette_action_selected", {
        action_type: "skill",
        action_value: skillId,
      });
      navigateToSkill("skills", skillId);
      close();
    },
    [navigateToSkill, close, posthog],
  );

  const openMemory = useCallback(() => {
    // PostHog: Capture command palette action selection
    posthog?.capture("command_palette_action_selected", {
      action_type: "memory",
      action_value: "memory_page",
    });
    navigateTo("memory");
    close();
  }, [navigateTo, close, posthog]);

  // ── Limit memory results shown (cap at 6 to keep palette fast) ──
  const displayMemories = useMemo(() => {
    return allMemories.slice(0, 8);
  }, [allMemories]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[100]">
      {/* Overlay */}
      <div
        className="absolute inset-0"
        style={{ backgroundColor: "var(--glass-overlay)" }}
        onClick={close}
        aria-hidden="true"
      />

      {/* Command dialog */}
      <div className="absolute inset-0 flex items-start justify-center pt-[min(20vh,120px)] px-4">
        <div
          className="w-full max-w-[560px] overflow-hidden border border-border-subtle"
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
            {/* Search input */}
            <div className="flex items-center gap-2 px-4 border-b border-border-subtle">
              <Search className="size-4 text-muted-foreground shrink-0" />
              <Command.Input
                value={search}
                onValueChange={setSearch}
                placeholder="Search skills, memories, pages, actions..."
                className="flex-1 h-12 bg-transparent text-foreground placeholder:text-muted-foreground outline-none border-0"
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

            {/* Results */}
            <Command.List
              className="max-h-[360px] overflow-y-auto p-2"
              style={{ overscrollBehavior: "contain" }}
            >
              <Command.Empty
                className="py-8 text-center text-muted-foreground"
                style={typo.caption}
              >
                No results found.
              </Command.Empty>

              {/* ── Pages ─────────────────────────────────────── */}
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
                  return (
                    <Command.Item
                      key={page.key}
                      value={`page ${page.label}`}
                      onSelect={() => navigateToPage(page.key)}
                      className={cn(
                        "flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer transition-colors min-h-[40px]",
                        "text-foreground data-[selected=true]:bg-muted",
                      )}
                    >
                      <Icon className="size-4 text-muted-foreground shrink-0" />
                      <span style={typo.label}>{page.label}</span>
                    </Command.Item>
                  );
                })}
              </Command.Group>

              {/* ── Skills ────────────────────────────────────── */}
              <Command.Group
                heading={
                  <span
                    className="text-muted-foreground px-2 pb-1"
                    style={typo.micro}
                  >
                    Skills
                  </span>
                }
              >
                {allSkills.map((skill) => (
                  <Command.Item
                    key={skill.id}
                    value={`skill ${skill.displayName} ${skill.domain} ${skill.tags.join(" ")}`}
                    onSelect={() => openSkill(skill.id)}
                    className={cn(
                      "flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer transition-colors min-h-[40px]",
                      "text-foreground data-[selected=true]:bg-muted",
                    )}
                  >
                    <FileText className="size-4 text-muted-foreground shrink-0" />
                    <div className="flex-1 min-w-0">
                      <span className="text-foreground" style={typo.label}>
                        {skill.displayName}
                      </span>
                      <span
                        className="text-muted-foreground ml-2"
                        style={typo.helper}
                      >
                        {skill.domain}
                      </span>
                    </div>
                    <span
                      className="text-muted-foreground shrink-0"
                      style={{
                        ...typo.mono,
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      v{skill.version}
                    </span>
                  </Command.Item>
                ))}
              </Command.Group>

              {/* ── Memories ──────────────────────────────────── */}
              <Command.Group
                heading={
                  <span
                    className="text-muted-foreground px-2 pb-1"
                    style={typo.micro}
                  >
                    Memories
                  </span>
                }
              >
                {displayMemories.map((mem) => {
                  const MemIcon = MEMORY_TYPE_ICON[mem.type];
                  const typeLabel = MEMORY_TYPE_LABEL[mem.type];
                  // Truncate content for the palette display
                  const preview =
                    mem.content.length > 80
                      ? mem.content.slice(0, 77) + "\u2026"
                      : mem.content;

                  return (
                    <Command.Item
                      key={mem.id}
                      value={`memory ${typeLabel} ${mem.content} ${mem.source} ${mem.tags.join(" ")}`}
                      onSelect={openMemory}
                      className={cn(
                        "flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer transition-colors min-h-[40px]",
                        "text-foreground data-[selected=true]:bg-muted",
                      )}
                    >
                      <MemIcon className="size-4 text-muted-foreground shrink-0" />
                      <div className="flex-1 min-w-0">
                        <span
                          className="text-foreground truncate block"
                          style={typo.caption}
                        >
                          {preview}
                        </span>
                      </div>
                      <div className="flex items-center gap-1.5 shrink-0">
                        {mem.pinned && (
                          <Pin className="size-3 text-accent fill-accent" />
                        )}
                        <span
                          className="text-muted-foreground px-1.5 py-0.5 rounded bg-muted"
                          style={typo.micro}
                        >
                          {typeLabel}
                        </span>
                      </div>
                    </Command.Item>
                  );
                })}
              </Command.Group>

              {/* ── Actions ───────────────────────────────────── */}
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
                    posthog?.capture("command_palette_action_selected", {
                      action_type: "action",
                      action_value: "new_session",
                    });
                    newSession();
                    navigate("/");
                    close();
                  }}
                  className={cn(
                    "flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer transition-colors min-h-[40px]",
                    "text-foreground data-[selected=true]:bg-muted",
                  )}
                >
                  <Plus className="size-4 text-muted-foreground shrink-0" />
                  <span style={typo.label}>New Session</span>
                </Command.Item>

                <Command.Item
                  value={`action toggle theme ${isDark ? "light" : "dark"} mode`}
                  onSelect={() => {
                    posthog?.capture("command_palette_action_selected", {
                      action_type: "action",
                      action_value: "toggle_theme",
                      new_theme: isDark ? "light" : "dark",
                    });
                    toggleTheme();
                    close();
                  }}
                  className={cn(
                    "flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer transition-colors min-h-[40px]",
                    "text-foreground data-[selected=true]:bg-muted",
                  )}
                >
                  {isDark ? (
                    <Sun className="size-4 text-muted-foreground shrink-0" />
                  ) : (
                    <Moon className="size-4 text-muted-foreground shrink-0" />
                  )}
                  <span style={typo.label}>
                    Switch to {isDark ? "Light" : "Dark"} Mode
                  </span>
                </Command.Item>

                <Command.Item
                  value="action open settings preferences"
                  onSelect={() => {
                    posthog?.capture("command_palette_action_selected", {
                      action_type: "action",
                      action_value: "open_settings",
                    });
                    navigate("/settings");
                    close();
                  }}
                  className={cn(
                    "flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer transition-colors min-h-[40px]",
                    "text-foreground data-[selected=true]:bg-muted",
                  )}
                >
                  <Settings className="size-4 text-muted-foreground shrink-0" />
                  <span style={typo.label}>Open Settings</span>
                </Command.Item>
              </Command.Group>
            </Command.List>

            {/* Footer hint */}
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

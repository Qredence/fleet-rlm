/**
 * PromptToolbar — chip row rendered inside the expanded PromptInput.
 *
 * Shows contextual chips for active features:
 *   - Mode chip: "Agent" / "Auto" / etc. with mode dropdown
 *   - Context Memory chip: "Logged in" with session info dropdown
 *   - Skills/Apps chip: "Apps" with skill list dropdown
 *   - Web Search chip: accent-coloured indicator when web search is active
 *
 * Each chip is a small pill button that opens a Popover on click.
 */
import { useState } from "react";
import {
  Bot,
  ChevronDown,
  Grid2x2,
  Brain,
  Zap,
  Globe,
  Users,
  Pencil,
  Check,
  Circle,
  Search,
} from "lucide-react";
import { typo } from "../config/typo";
import type { PromptFeature, PromptMode } from "../data/types";
import { Popover, PopoverContent, PopoverTrigger } from "./popover";
import { ScrollArea } from "./scroll-area";
import { cn } from "./utils";
import { UNSUPPORTED_SECTION_REASON } from "../../lib/rlm-api";

// ── Re-export for consumers ─────────────────────────────────────────

export type { PromptMode };

// ── Types ───────────────────────────────────────────────────────────

interface PromptToolbarProps {
  activeFeatures: Set<PromptFeature>;
  mode: PromptMode;
  onSetMode: (mode: PromptMode) => void;
  selectedSkills: string[];
  onToggleSkill: (skillId: string) => void;
}

// ── Mode definitions ────────────────────────────────────────────────

const modeItems: {
  key: PromptMode;
  label: string;
  icon: typeof Bot;
  description: string;
}[] = [
  {
    key: "auto",
    label: "Auto",
    icon: Zap,
    description: "Automatically determine the best approach",
  },
  {
    key: "skillCreation",
    label: "Skill Creation",
    icon: Pencil,
    description: "Create and iterate on skill definitions",
  },
  {
    key: "webSearch",
    label: "Web Search",
    icon: Globe,
    description: "Search and reference web sources",
  },
  {
    key: "cowork",
    label: "Cowork",
    icon: Users,
    description: "Collaborative multi-agent workflow",
  },
];

const modeDisplayLabel: Record<PromptMode, string> = {
  auto: "Agent",
  skillCreation: "Skill Creation",
  webSearch: "Web Search",
  cowork: "Cowork",
};

// ── Chip wrapper ────────────────────────────────────────────────────

function ToolbarChip({
  icon: Icon,
  label,
  hasChevron = false,
  variant = "default",
  className,
  onClick,
}: {
  icon: typeof Bot;
  label: string;
  hasChevron?: boolean;
  /** 'accent' renders chip with accent styling for active indicators */
  variant?: "default" | "accent";
  className?: string;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      className={cn(
        "flex items-center gap-1.5 h-7 px-2.5 rounded-full transition-colors shrink-0",
        "focus-visible:outline-none focus-visible:ring-[2px] focus-visible:ring-ring/50",
        variant === "accent"
          ? "bg-accent/10 text-accent"
          : "bg-muted/60 hover:bg-muted text-foreground",
        className,
      )}
      onClick={onClick}
    >
      <Icon
        className={cn(
          "size-3.5 shrink-0",
          variant === "accent" ? "text-accent" : "text-muted-foreground",
        )}
      />
      <span style={typo.helper}>{label}</span>
      {hasChevron && (
        <ChevronDown
          className={cn(
            "size-3 shrink-0",
            variant === "accent" ? "text-accent" : "text-muted-foreground",
          )}
        />
      )}
    </button>
  );
}

// ── Mode dropdown ───────────────────────────────────────────────────

function ModeChip({
  mode,
  onSetMode,
}: {
  mode: PromptMode;
  onSetMode: (m: PromptMode) => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <span className="inline-flex">
          <ToolbarChip icon={Bot} label={modeDisplayLabel[mode]} hasChevron />
        </span>
      </PopoverTrigger>
      <PopoverContent
        side="top"
        align="start"
        sideOffset={8}
        className="w-[260px] p-1.5 rounded-xl"
      >
        <div className="mb-1.5 px-2">
          <span className="text-muted-foreground" style={typo.helper}>
            Mode
          </span>
        </div>
        {modeItems.map((item) => {
          const Icon = item.icon;
          const isActive = mode === item.key;

          return (
            <button
              key={item.key}
              type="button"
              className={cn(
                "flex items-center gap-2.5 w-full px-2.5 py-2 rounded-lg transition-colors",
                "focus-visible:outline-none focus-visible:ring-[2px] focus-visible:ring-ring/50",
                isActive
                  ? "bg-accent/8 text-accent"
                  : "text-foreground hover:bg-muted",
              )}
              onClick={() => {
                onSetMode(item.key);
                setOpen(false);
              }}
            >
              <Icon
                className={cn(
                  "size-4 shrink-0",
                  isActive ? "text-accent" : "text-muted-foreground",
                )}
              />
              <div className="flex-1 min-w-0 text-left">
                <span className="block" style={typo.label}>
                  {item.label}
                </span>
                <span
                  className="text-muted-foreground block"
                  style={typo.micro}
                >
                  {item.description}
                </span>
              </div>
              {isActive && <Check className="size-4 text-accent shrink-0" />}
            </button>
          );
        })}
      </PopoverContent>
    </Popover>
  );
}

// ── Context Memory chip ─────────────────────────────────────────────

function ContextMemoryChip() {
  const [open, setOpen] = useState(false);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <span className="inline-flex">
          <ToolbarChip icon={Brain} label="Logged in" hasChevron />
        </span>
      </PopoverTrigger>
      <PopoverContent
        side="top"
        align="start"
        sideOffset={8}
        className="w-[260px] p-3 rounded-xl"
      >
        <div className="mb-2">
          <span className="text-foreground block" style={typo.label}>
            Context Memory
          </span>
          <span className="text-muted-foreground" style={typo.helper}>
            Python REPL sandbox active
          </span>
        </div>

        <div className="space-y-2">
          <div className="flex items-center gap-2 p-2 rounded-lg bg-muted/50">
            <Circle
              className="size-2 shrink-0"
              fill="var(--chart-3)"
              stroke="none"
            />
            <span className="text-foreground" style={typo.caption}>
              Session active
            </span>
          </div>
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground" style={typo.helper}>
                Runtime
              </span>
              <span className="text-foreground" style={typo.helper}>
                Python 3.12
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground" style={typo.helper}>
                Memory
              </span>
              <span className="text-foreground" style={typo.helper}>
                128 MB allocated
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground" style={typo.helper}>
                Documents loaded
              </span>
              <span className="text-foreground" style={typo.helper}>
                3 files
              </span>
            </div>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}

// ── Web Search chip (accent indicator) ──────────────────────────────

function WebSearchChip() {
  return <ToolbarChip icon={Search} label="Search" variant="accent" />;
}

// ── Skills / Apps chip ──────────────────────────────────────────────

function SkillsChip({
  selectedSkills,
}: {
  selectedSkills: string[];
}) {
  const [open, setOpen] = useState(false);
  const selectedCount = selectedSkills.length;
  const chipLabel = selectedCount > 0 ? `Apps (${selectedCount})` : "Apps";

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <span className="inline-flex">
          <ToolbarChip icon={Grid2x2} label={chipLabel} hasChevron />
        </span>
      </PopoverTrigger>
      <PopoverContent
        side="top"
        align="start"
        sideOffset={8}
        className="w-[280px] p-1.5 rounded-xl"
      >
        <div className="mb-1.5 px-2 flex items-center justify-between">
          <span className="text-muted-foreground" style={typo.helper}>
            Skills & Capabilities
          </span>
          {selectedCount > 0 && (
            <span className="text-accent" style={typo.micro}>
              {selectedCount} selected
            </span>
          )}
        </div>

        <ScrollArea className="max-h-[240px]">
          <div className="px-2.5 py-3">
            <span className="text-muted-foreground block" style={typo.caption}>
              Skills selection is unavailable. {UNSUPPORTED_SECTION_REASON}
            </span>
          </div>
        </ScrollArea>
      </PopoverContent>
    </Popover>
  );
}

// ── Main toolbar ────────────────────────────────────────────────────

export function PromptToolbar({
  activeFeatures,
  mode,
  onSetMode,
  selectedSkills,
  onToggleSkill: _onToggleSkill,
}: PromptToolbarProps) {
  const showContextMemory = activeFeatures.has("contextMemory");
  const showSkills = activeFeatures.has("skills");
  const showWebSearch = activeFeatures.has("webSearch");

  return (
    <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar">
      {/* Mode chip — always visible when toolbar is shown */}
      <ModeChip mode={mode} onSetMode={onSetMode} />

      {/* Context Memory session status */}
      {showContextMemory && <ContextMemoryChip />}

      {/* Web Search active indicator */}
      {showWebSearch && <WebSearchChip />}

      {/* Skills / Apps selector */}
      {showSkills && (
        <SkillsChip selectedSkills={selectedSkills} />
      )}
    </div>
  );
}

/**
 * CanvasSwitcher — popover dropdown triggered by the PanelHeaderChip
 * in the BuilderPanel header. Lets users quick-switch between canvas
 * views (Volume Browser, Code Sandbox) and recently viewed skills.
 *
 * All navigation goes through useAppNavigate / useNavigation so the
 * URL remains the single source of truth.
 */
import { useState, useMemo, type ReactNode } from "react";
import { HardDrive, Brain, FileText, Check, Layers } from "lucide-react";
import { typo } from "@/lib/config/typo";
import { cn } from "@/lib/utils/cn";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Popover,
  PopoverTrigger,
  PopoverContent,
} from "@/components/ui/popover";
import { PanelHeaderChip } from "@/features/artifacts/PanelHeaderChip";
import type { Skill } from "@/lib/data/types";

/* ── Types ─────────────────────────────────────────────────────────── */

export type CanvasMode =
  | "creation"
  | "detail"
  | "volumes-browser"
  | "code-artifact"
  | "file-detail"
  | "empty";

interface CanvasViewItem {
  id: CanvasMode;
  label: string;
  icon: ReactNode;
  /** When true, this item appears dimmed and un-clickable */
  disabled?: boolean;
}

export interface CanvasSwitcherProps {
  /** Current canvas mode — determines which item is highlighted. */
  canvasMode: CanvasMode;
  /** Icon to display on the chip trigger. */
  headerIcon?: ReactNode;
  /** Label for the chip trigger. */
  headerLabel: string;
  /** Version string — shown as a mono Badge when present. */
  version?: string;
  /** Currently selected skill (if any) — excluded from recent list. */
  selectedSkill?: Skill | null;
  /** All available skills — top recents are shown. */
  skills: Skill[];
  /** Called when user picks a canvas view from the list. */
  onSelectView: (mode: CanvasMode) => void;
  /** Called when user picks a skill from the recent list. */
  onSelectSkill: (skillId: string) => void;
}

/* ── Static view definitions ─────────────────────────────────────── */

const CANVAS_VIEWS: CanvasViewItem[] = [
  {
    id: "volumes-browser",
    label: "Volume Browser",
    icon: (
      <HardDrive className="size-4 text-muted-foreground" aria-hidden="true" />
    ),
  },
  {
    id: "code-artifact",
    label: "Code Sandbox",
    icon: <Brain className="size-4 text-accent" aria-hidden="true" />,
  },
  {
    id: "creation",
    label: "Execution",
    icon: <FileText className="size-4 text-chart-3" aria-hidden="true" />,
  },
];

const MAX_RECENT_SKILLS = 5;

/* ── Switcher Row (internal) ─────────────────────────────────────── */

function SwitcherRow({
  icon,
  label,
  trailing,
  active,
  disabled,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  trailing?: ReactNode;
  active?: boolean;
  disabled?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "flex items-center gap-3 w-full px-3 py-2 rounded-lg",
        "transition-colors duration-100",
        "text-left",
        active
          ? "bg-accent/8 text-foreground"
          : "text-foreground hover:bg-muted/60",
        disabled && "opacity-40 cursor-not-allowed",
      )}
    >
      <span className="shrink-0 flex items-center justify-center w-5 h-5">
        {icon}
      </span>
      <span className="flex-1 truncate" style={typo.label}>
        {label}
      </span>
      {trailing}
      {active && (
        <Check className="size-4 text-accent shrink-0" aria-hidden="true" />
      )}
    </button>
  );
}

/* ── Main Component ──────────────────────────────────────────────── */

export function CanvasSwitcher({
  canvasMode,
  headerIcon,
  headerLabel,
  version,
  selectedSkill,
  skills,
  onSelectView,
  onSelectSkill,
}: CanvasSwitcherProps) {
  const [open, setOpen] = useState(false);

  // Recent skills — exclude currently selected, take top N by lastUsed
  const recentSkills = useMemo(() => {
    return skills
      .filter((s) => s.id !== selectedSkill?.id)
      .sort(
        (a, b) =>
          new Date(b.lastUsed).getTime() - new Date(a.lastUsed).getTime(),
      )
      .slice(0, MAX_RECENT_SKILLS);
  }, [skills, selectedSkill?.id]);

  const handleSelectView = (mode: CanvasMode) => {
    onSelectView(mode);
    setOpen(false);
  };

  const handleSelectSkill = (skillId: string) => {
    onSelectSkill(skillId);
    setOpen(false);
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <span className="inline-flex min-w-0">
          <PanelHeaderChip
            icon={headerIcon}
            label={headerLabel}
            version={version}
            open={open}
            interactive
          />
        </span>
      </PopoverTrigger>

      <PopoverContent
        align="start"
        sideOffset={8}
        className={cn(
          "w-72 p-0 rounded-card border border-border-subtle",
          "shadow-md overflow-hidden",
        )}
      >
        <ScrollArea className="max-h-80">
          <div className="p-2">
            {/* ── Canvas Views ──────────────────────────────────── */}
            <div className="px-2 py-1.5">
              <span className="text-muted-foreground" style={typo.micro}>
                CANVAS VIEWS
              </span>
            </div>
            {CANVAS_VIEWS.map((view) => (
              <SwitcherRow
                key={view.id}
                icon={view.icon}
                label={view.label}
                active={canvasMode === view.id}
                disabled={view.disabled}
                onClick={() => handleSelectView(view.id)}
              />
            ))}

            {/* ── Separator ────────────────────────────────────── */}
            {recentSkills.length > 0 && (
              <>
                <Separator className="my-2 bg-border-subtle" />

                {/* ── Recent Skills ──────────────────────────────── */}
                <div className="px-2 py-1.5">
                  <span className="text-muted-foreground" style={typo.micro}>
                    RECENT SKILLS
                  </span>
                </div>
                {recentSkills.map((skill) => (
                  <SwitcherRow
                    key={skill.id}
                    icon={
                      <Layers
                        className="size-4 text-muted-foreground"
                        aria-hidden="true"
                      />
                    }
                    label={skill.displayName}
                    trailing={
                      <Badge
                        variant="secondary"
                        className="rounded-full shrink-0"
                        style={typo.helper}
                      >
                        {skill.domain}
                      </Badge>
                    }
                    active={
                      canvasMode === "detail" && selectedSkill?.id === skill.id
                    }
                    onClick={() => handleSelectSkill(skill.id)}
                  />
                ))}
              </>
            )}

            {/* ── Currently viewing skill ──────────────────────── */}
            {selectedSkill && canvasMode === "detail" && (
              <>
                <Separator className="my-2 bg-border-subtle" />
                <div className="px-2 py-1.5">
                  <span className="text-muted-foreground" style={typo.micro}>
                    CURRENT SKILL
                  </span>
                </div>
                <SwitcherRow
                  icon={
                    <Layers className="size-4 text-accent" aria-hidden="true" />
                  }
                  label={selectedSkill.displayName}
                  trailing={
                    <Badge
                      variant="secondary"
                      className="rounded-full shrink-0"
                      style={typo.mono}
                    >
                      v{selectedSkill.version}
                    </Badge>
                  }
                  active
                />
              </>
            )}
          </div>
        </ScrollArea>
      </PopoverContent>
    </Popover>
  );
}

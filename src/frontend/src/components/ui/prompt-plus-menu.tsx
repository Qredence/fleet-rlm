/**
 * PromptPlusMenu — popover from the "+" button in the PromptInput.
 *
 * Allows toggling features: Library, Context Memory, Skills, Web search.
 * Active features are shown with accent color and a checkmark.
 * "More" is a placeholder that shows a "Coming soon" toast.
 *
 * Uses Popover (not DropdownMenu) so the menu stays open when toggling
 * multiple features in a single interaction.
 */
import { useState } from "react";
import {
  BookOpen,
  Brain,
  Sparkles,
  Globe,
  MoreHorizontal,
  ChevronRight,
  Check,
  Plus,
} from "lucide-react";
import { toast } from "sonner";
import { typo } from "@/lib/config/typo";
import type { PromptFeature } from "@/lib/data/types";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { IconButton } from "@/components/ui/icon-button";
import { cn } from "@/lib/utils/cn";

// ── Types ───────────────────────────────────────────────────────────

export type { PromptFeature };

interface PromptPlusMenuProps {
  activeFeatures: Set<PromptFeature>;
  onToggleFeature: (feature: PromptFeature) => void;
  /** Override class on the trigger button */
  className?: string;
}

// ── Feature definitions ─────────────────────────────────────────────

const featureItems: {
  key: PromptFeature;
  label: string;
  icon: typeof Brain;
}[] = [
  { key: "library", label: "Library", icon: BookOpen },
  { key: "contextMemory", label: "Context Memory", icon: Brain },
  { key: "skills", label: "Skills", icon: Sparkles },
  { key: "webSearch", label: "Web search", icon: Globe },
];

// ── Component ───────────────────────────────────────────────────────

export function PromptPlusMenu({
  activeFeatures,
  onToggleFeature,
  className,
}: PromptPlusMenuProps) {
  const [open, setOpen] = useState(false);
  const hasActive = activeFeatures.size > 0;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <span className="inline-flex">
          <IconButton
            type="button"
            className={cn("touch-target rounded-full", className)}
            isActive={hasActive}
            aria-label="Prompt features"
          >
            <Plus
              className={cn(
                "size-5",
                hasActive ? "text-accent" : "text-foreground",
              )}
            />
          </IconButton>
        </span>
      </PopoverTrigger>

      <PopoverContent
        side="top"
        align="start"
        sideOffset={8}
        className="w-55 p-1.5 rounded-xl"
      >
        {featureItems.map((item) => {
          const Icon = item.icon;
          const isActive = activeFeatures.has(item.key);

          return (
            <button
              key={item.key}
              type="button"
              className={cn(
                "flex items-center gap-2.5 w-full px-2.5 py-2 rounded-lg transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
                isActive
                  ? "text-accent bg-accent/8"
                  : "text-foreground hover:bg-muted",
              )}
              onClick={() => onToggleFeature(item.key)}
              aria-pressed={isActive}
            >
              <Icon
                className={cn(
                  "size-4.5 shrink-0",
                  isActive ? "text-accent" : "text-muted-foreground",
                )}
              />
              <span className="flex-1 text-left" style={typo.label}>
                {item.label}
              </span>
              {isActive && <Check className="size-4 text-accent shrink-0" />}
            </button>
          );
        })}

        {/* Separator */}
        <div
          className="my-1 h-px mx-1"
          style={{ backgroundColor: "var(--border-subtle)" }}
        />

        {/* More — placeholder */}
        <button
          type="button"
          className={cn(
            "flex items-center gap-2.5 w-full px-2.5 py-2 rounded-lg transition-colors",
            "text-foreground hover:bg-muted",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
          )}
          onClick={() => {
            toast("More features coming soon", {
              description:
                "Additional prompt modes and tools are on the roadmap.",
            });
            setOpen(false);
          }}
        >
          <MoreHorizontal className="size-4.5 text-muted-foreground shrink-0" />
          <span className="flex-1 text-left" style={typo.label}>
            More
          </span>
          <ChevronRight className="size-4 text-muted-foreground shrink-0" />
        </button>
      </PopoverContent>
    </Popover>
  );
}

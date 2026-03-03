import { motion, useReducedMotion } from "motion/react";
import { typo } from "@/lib/config/typo";
import type { Skill } from "@/lib/data/types";
import { springs } from "@/lib/config/motion-config";
import { Card } from "@/components/ui/card";
import { cn } from "@/components/ui/utils";
import { SkillBadge } from "./SkillBadge";

const statusVariantMap: Record<
  string,
  "accent" | "success" | "secondary" | "warning" | "destructive-subtle"
> = {
  published: "accent",
  validated: "success",
  draft: "secondary",
  validating: "warning",
  deprecated: "destructive-subtle",
};

interface Props {
  skill: Skill;
  isSelected: boolean;
  onSelect: () => void;
  className?: string;
}

export function SkillCard({ skill, isSelected, onSelect, className }: Props) {
  const badgeVariant = statusVariantMap[skill.status] || "secondary";
  const prefersReduced = useReducedMotion();
  const transition = prefersReduced ? springs.instant : springs.snappy;

  return (
    <motion.div
      whileHover={prefersReduced ? undefined : { y: -2 }}
      whileTap={prefersReduced ? undefined : { scale: 0.98 }}
      transition={transition}
      className="min-w-0"
      style={{ borderRadius: "var(--radius-card)" }}
    >
      <Card
        className={cn(
          "cursor-pointer border-border-subtle transition-shadow duration-200 hover:shadow-md hover:bg-muted overflow-hidden",
          isSelected ? "border-accent bg-accent/5" : "",
          className,
        )}
        style={{
          boxShadow: isSelected
            ? "0 0 0 1.5px color-mix(in srgb, var(--accent) 50%, transparent), 0 4px 16px color-mix(in srgb, var(--accent) 8%, transparent)"
            : undefined,
        }}
        onClick={onSelect}
      >
        <div className="px-4 py-3 flex flex-col gap-2 min-w-0">
          {/* Row 1: Name + Status */}
          <div className="flex items-center justify-between gap-2 min-w-0">
            <h3 className="text-foreground truncate min-w-0" style={typo.label}>
              {skill.displayName}
            </h3>
            <SkillBadge label={skill.status} variant={badgeVariant} />
          </div>

          {/* Row 2: Description (single line) */}
          <p
            className="text-muted-foreground line-clamp-1"
            style={typo.caption}
          >
            {skill.description}
          </p>

          {/* Row 3: Domain + Version + Quality */}
          <div className="flex items-center gap-2">
            <SkillBadge label={skill.domain} variant="secondary" />
            <span className="text-muted-foreground" style={typo.mono}>
              v{skill.version}
            </span>
            <span className="text-muted-foreground ml-auto" style={typo.helper}>
              {skill.qualityScore}%
            </span>
          </div>
        </div>
      </Card>
    </motion.div>
  );
}

import { useState } from "react";
import { ArrowUpRight, Eye, GitBranch, GitFork, Tag } from "lucide-react";
import { typo } from "@/lib/config/typo";
import type { Skill } from "@/lib/data/types";
import { useSkillContent } from "@/hooks/useSkills";
import { SkillMarkdown } from "@/components/shared/SkillMarkdown";
import { Badge } from "@/components/ui/badge";
import {
  PanelTabs,
  PanelTabList,
  PanelTabTrigger,
  PanelTabContent,
  PanelTabPanels,
} from "@/components/ui/panel-tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils/cn";

// ── Types ───────────────────────────────────────────────────────────
interface Props {
  skill: Skill;
  className?: string;
}

// ── Status helpers ──────────────────────────────────────────────────
const statusColorMap: Record<string, string> = {
  published: "var(--chart-3)",
  validated: "var(--chart-1)",
  validating: "var(--chart-5)",
  draft: "var(--muted-foreground)",
  deprecated: "var(--chart-4)",
};

function StatusDot({ status }: { status: string }) {
  return (
    <span
      className="inline-block size-[7px] rounded-full shrink-0"
      style={{
        backgroundColor: statusColorMap[status] ?? "var(--muted-foreground)",
      }}
    />
  );
}

// ── Quality score color ─────────────────────────────────────────────
function scoreColor(score: number): string {
  if (score >= 90) return "var(--chart-3)";
  if (score >= 80) return "var(--chart-1)";
  if (score >= 70) return "var(--chart-5)";
  return "var(--chart-4)";
}

// ── Date formatting helper ──────────────────────────────────────────
const dateFormatter = new Intl.DateTimeFormat(undefined, {
  year: "numeric",
  month: "short",
  day: "numeric",
});

function formatDate(dateStr: string): string {
  try {
    return dateFormatter.format(new Date(dateStr));
  } catch {
    return dateStr;
  }
}

// ── Metadata section group ──────────────────────────────────────────
function MetadataGroup({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-0">
      <p
        className="text-muted-foreground/60 pb-2 pt-1 uppercase"
        style={{
          ...typo.micro,
          letterSpacing: "0.06em",
        }}
      >
        {label}
      </p>
      <div className="divide-y divide-border-subtle">{children}</div>
    </div>
  );
}

// ── Metadata row ────────────────────────────────────────────────────
function MetadataRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between py-2.5">
      <span className="text-muted-foreground" style={typo.label}>
        {label}
      </span>
      <span className="text-foreground text-right" style={typo.label}>
        {children}
      </span>
    </div>
  );
}

// ── Main Component ──────────────────────────────────────────────────
export function SkillDetail({ skill, className }: Props) {
  const [tab, setTab] = useState("preview");
  const { content: skillContent } = useSkillContent(skill.id);
  const depCount = skill.dependencies.length;

  return (
    <PanelTabs
      value={tab}
      onValueChange={setTab}
      layoutId="skillDetailTabs"
      className={className || "flex flex-col h-full"}
    >
      <PanelTabList>
        <PanelTabTrigger value="preview" icon={<Eye />}>
          Preview
        </PanelTabTrigger>
        <PanelTabTrigger value="metadata" icon={<Tag />}>
          Metadata
        </PanelTabTrigger>
        <PanelTabTrigger
          value="deps"
          icon={<GitFork />}
          badge={depCount > 0 ? depCount : undefined}
        >
          Dependencies
        </PanelTabTrigger>
      </PanelTabList>

      <ScrollArea className="flex-1 min-h-0">
        <PanelTabPanels>
          {/* ── Preview ──────────────────────────────────────────── */}
          <PanelTabContent value="preview" className="p-4 md:p-6">
            <SkillMarkdown content={skillContent} />
          </PanelTabContent>

          {/* ── Metadata ─────────────────────────────────────────── */}
          <PanelTabContent value="metadata" className="p-4 md:p-6 space-y-5">
            {/* Identity */}
            <MetadataGroup label="Identity">
              <MetadataRow label="Name">
                <code
                  className="bg-muted px-1.5 py-0.5 rounded"
                  style={typo.mono}
                >
                  {skill.name}
                </code>
              </MetadataRow>
              <MetadataRow label="Display Name">
                {skill.displayName}
              </MetadataRow>
              <MetadataRow label="Version">
                <code
                  className="bg-muted px-1.5 py-0.5 rounded"
                  style={typo.mono}
                >
                  v{skill.version}
                </code>
              </MetadataRow>
              <MetadataRow label="Author">{skill.author}</MetadataRow>
              <MetadataRow label="Created">
                {formatDate(skill.createdAt)}
              </MetadataRow>
            </MetadataGroup>

            {/* Classification */}
            <MetadataGroup label="Classification">
              <MetadataRow label="Domain">
                <Badge
                  variant="secondary"
                  className="rounded-full"
                  style={typo.helper}
                >
                  {skill.domain}
                </Badge>
              </MetadataRow>
              <MetadataRow label="Category">
                <Badge
                  variant="secondary"
                  className="rounded-full"
                  style={typo.helper}
                >
                  {skill.category}
                </Badge>
              </MetadataRow>
              <MetadataRow label="Status">
                <span className="inline-flex items-center gap-1.5">
                  <StatusDot status={skill.status} />
                  <span
                    style={{
                      ...typo.label,
                      color:
                        statusColorMap[skill.status] ?? "var(--foreground)",
                    }}
                  >
                    {skill.status.charAt(0).toUpperCase() +
                      skill.status.slice(1)}
                  </span>
                </span>
              </MetadataRow>
            </MetadataGroup>

            {/* Performance */}
            <MetadataGroup label="Performance">
              <MetadataRow label="Quality Score">
                <span className="inline-flex items-center gap-2">
                  {/* Mini bar */}
                  <span className="relative w-12 h-[5px] rounded-full bg-muted overflow-hidden">
                    <span
                      className="absolute inset-y-0 left-0 rounded-full transition-[width]"
                      style={{
                        width: `${skill.qualityScore}%`,
                        backgroundColor: scoreColor(skill.qualityScore),
                      }}
                    />
                  </span>
                  <span
                    style={{
                      ...typo.label,
                      color: scoreColor(skill.qualityScore),
                      fontFamily: "var(--font-family-mono)",
                    }}
                  >
                    {skill.qualityScore}%
                  </span>
                </span>
              </MetadataRow>
              <MetadataRow label="Usage Count">
                {skill.usageCount.toLocaleString()}
              </MetadataRow>
              <MetadataRow label="Last Used">
                {formatDate(skill.lastUsed)}
              </MetadataRow>
            </MetadataGroup>

            {/* Tags */}
            <div className="space-y-2 pt-1">
              <p
                className="text-muted-foreground/60 uppercase"
                style={{
                  ...typo.micro,
                  letterSpacing: "0.06em",
                }}
              >
                Tags
              </p>
              <div className="flex flex-wrap gap-1.5">
                {skill.tags.map((tag) => (
                  <Badge
                    key={tag}
                    variant="secondary"
                    className="rounded-full"
                    style={typo.helper}
                  >
                    {tag}
                  </Badge>
                ))}
              </div>
            </div>

            {/* Taxonomy Path */}
            <div className="space-y-2 pt-1">
              <p
                className="text-muted-foreground/60 uppercase"
                style={{
                  ...typo.micro,
                  letterSpacing: "0.06em",
                }}
              >
                Taxonomy Path
              </p>
              <div className="flex items-center gap-2">
                <GitBranch
                  className="size-4 shrink-0"
                  style={{ color: "var(--accent)" }}
                />
                <code
                  className="bg-accent/5 px-2 py-1 rounded"
                  style={{
                    ...typo.mono,
                    color: "var(--accent)",
                  }}
                >
                  {skill.taxonomyPath}
                </code>
              </div>
            </div>
          </PanelTabContent>

          {/* ── Dependencies ─────────────────────────────────────── */}
          <PanelTabContent value="deps" className="p-4 md:p-6 space-y-3">
            {depCount > 0 ? (
              <>
                <p className="text-muted-foreground" style={typo.labelRegular}>
                  This skill depends on {depCount} prerequisite
                  {depCount !== 1 ? "s" : ""}:
                </p>
                <div className="space-y-2">
                  {skill.dependencies.map((dep) => (
                    <div
                      key={dep}
                      className={cn(
                        "group flex items-center gap-3 p-3 rounded-lg",
                        "border border-border-subtle bg-card",
                        "hover:border-accent/30 hover:bg-accent/[0.03] transition-colors",
                      )}
                    >
                      <span className="flex items-center justify-center size-8 rounded-lg shrink-0 bg-accent/10">
                        <GitFork
                          className="size-4"
                          style={{ color: "var(--accent)" }}
                        />
                      </span>
                      <div className="flex-1 min-w-0">
                        <p
                          className="text-foreground truncate"
                          style={typo.label}
                        >
                          {dep}
                        </p>
                        <div className="flex items-center gap-1.5 mt-0.5">
                          <StatusDot status="published" />
                          <span
                            className="text-muted-foreground"
                            style={typo.helper}
                          >
                            available
                          </span>
                        </div>
                      </div>
                      <ArrowUpRight className="size-4 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <div className="flex items-center justify-center size-10 rounded-lg bg-muted mb-3">
                  <GitFork className="size-5 text-muted-foreground" />
                </div>
                <p className="text-foreground mb-1" style={typo.label}>
                  No dependencies
                </p>
                <p className="text-muted-foreground" style={typo.caption}>
                  This skill is self-contained and has no prerequisites.
                </p>
              </div>
            )}
          </PanelTabContent>
        </PanelTabPanels>
      </ScrollArea>
    </PanelTabs>
  );
}

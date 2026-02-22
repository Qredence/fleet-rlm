import { useState, useEffect, useMemo } from "react";
import { motion, useReducedMotion } from "motion/react";
import { ClipboardList, Eye, Shield, ShieldCheck } from "lucide-react";
import { typo } from "@/lib/config/typo";
import { fades } from "@/lib/config/motion-config";
import type { CreationPhase } from "@/lib/data/types";
import {
  generatedSkillMd,
  mockPlanSteps,
  mockSkillMetadata,
} from "@/lib/data/mock-skills";
import { SkillMarkdown } from "@/components/shared/SkillMarkdown";
import { SectionHeader } from "@/components/shared/SectionHeader";
import { TypingDots } from "@/components/shared/TypingDots";
import { Card, CardContent } from "@/components/ui/card";
import {
  PanelTabs,
  PanelTabList,
  PanelTabTrigger,
  PanelTabContent,
  PanelTabPanels,
} from "@/components/ui/panel-tabs";
import { Progress } from "@/components/ui/progress";
import {
  Queue,
  QueueSection,
  QueueSectionTrigger,
  QueueSectionLabel,
  QueueSectionContent,
  QueueList,
  QueueItem,
  QueueItemIndicator,
  QueueItemContent,
  QueueItemDescription,
} from "@/components/ui/queue";
import { ScrollArea } from "@/components/ui/scroll-area";
import { PhaseIndicator } from "@/features/PhaseIndicator";

interface Props {
  phase: CreationPhase;
  className?: string;
}

export function CreationPreview({ phase, className }: Props) {
  const phaseOrder = [
    "idle",
    "understanding",
    "generating",
    "validating",
    "complete",
  ];
  const phaseIdx = phaseOrder.indexOf(phase);

  const activeTabFromPhase =
    phaseIdx >= 3 ? "validation" : phaseIdx >= 2 ? "preview" : "plan";

  const [activeTab, setActiveTab] = useState(activeTabFromPhase);

  // ── Track when generated content is ready to render ────────────
  // During 'generating' phase the preview tab initially shows a spinner.
  // After ~2.75 s (matching the chat simulation's 2.8 s "Content generation
  // complete" message) we flip this flag so the SKILL.md renders just as
  // the assistant announces completion. Once past the generating phase the
  // content is always considered ready.
  const [mdReady, setMdReady] = useState(phaseIdx > 2);
  const prefersReduced = useReducedMotion();

  useEffect(() => {
    setActiveTab(activeTabFromPhase);
  }, [activeTabFromPhase]);

  useEffect(() => {
    if (phase === "generating") {
      setMdReady(false);
      const timer = setTimeout(() => setMdReady(true), 2750);
      return () => clearTimeout(timer);
    }
    // Past generating → always ready
    if (phaseIdx > 2) {
      setMdReady(true);
    }
  }, [phase, phaseIdx]);

  // ── Progressive plan step completion ────────────────────────────
  // Simulate steps completing as phases progress.
  // understanding phase: first 2 steps complete progressively
  // generating+: all steps complete (plan is finalized)
  const planSteps = useMemo(() => {
    return mockPlanSteps.map((step, i) => {
      if (phaseIdx >= 2) {
        // generating or later → entire plan is finalized
        return { ...step, completed: true };
      }
      if (phase === "understanding") {
        // Reveal steps progressively: first 2 marked done during understanding
        return { ...step, completed: i < 2 };
      }
      return step;
    });
  }, [phase, phaseIdx]);

  const completedCount = planSteps.filter((s) => s.completed).length;

  return (
    <PanelTabs
      value={activeTab}
      onValueChange={setActiveTab}
      layoutId="creationTabs"
      className={className || "flex flex-col h-full"}
    >
      <PanelTabList>
        <PanelTabTrigger
          value="plan"
          icon={<ClipboardList />}
          disabled={phaseIdx < 1}
        >
          Plan
        </PanelTabTrigger>
        <PanelTabTrigger value="preview" icon={<Eye />} disabled={phaseIdx < 2}>
          Preview
        </PanelTabTrigger>
        <PanelTabTrigger
          value="validation"
          icon={<ShieldCheck />}
          disabled={phaseIdx < 3}
        >
          Validation
        </PanelTabTrigger>
      </PanelTabList>

      <ScrollArea className="flex-1 min-h-0">
        <PanelTabPanels>
          {/* ── Plan ─────────────────────────────────────────────── */}
          <PanelTabContent value="plan" className="p-4 md:p-6 space-y-4">
            <PhaseIndicator phase={phase} />

            <h3 className="text-foreground" style={typo.h3}>
              Skill Plan
            </h3>

            {phase === "understanding" && (
              <TypingDots label="Analyzing requirements\u2026" />
            )}

            {phaseIdx >= 1 && (
              <Queue>
                {/* ── Planning Steps ───────────────────────────── */}
                <QueueSection defaultOpen>
                  <QueueSectionTrigger>
                    <QueueSectionLabel
                      label="Planning Steps"
                      count={completedCount}
                    />
                  </QueueSectionTrigger>
                  <QueueSectionContent>
                    <QueueList>
                      {planSteps.map((step) => (
                        <QueueItem key={step.id}>
                          <QueueItemIndicator completed={step.completed} />
                          <QueueItemContent completed={step.completed}>
                            {step.label}
                          </QueueItemContent>
                          {step.description && (
                            <QueueItemDescription completed={step.completed}>
                              {step.description}
                            </QueueItemDescription>
                          )}
                        </QueueItem>
                      ))}
                    </QueueList>
                  </QueueSectionContent>
                </QueueSection>

                {/* ── Resolved Metadata (after plan is complete) ── */}
                {phaseIdx >= 2 && (
                  <QueueSection defaultOpen>
                    <QueueSectionTrigger>
                      <QueueSectionLabel
                        label="Skill Metadata"
                        count={mockSkillMetadata.length}
                      />
                    </QueueSectionTrigger>
                    <QueueSectionContent>
                      <QueueList>
                        {mockSkillMetadata.map(({ id, label, value }) => (
                          <QueueItem key={id}>
                            <QueueItemIndicator completed />
                            <QueueItemContent completed>
                              <span
                                className="text-muted-foreground"
                                style={typo.label}
                              >
                                {label}
                              </span>
                            </QueueItemContent>
                            <QueueItemDescription completed>
                              {value}
                            </QueueItemDescription>
                          </QueueItem>
                        ))}
                      </QueueList>
                    </QueueSectionContent>
                  </QueueSection>
                )}
              </Queue>
            )}
          </PanelTabContent>

          {/* ── Preview ──────────────────────────────────────────── */}
          <PanelTabContent value="preview" className="p-4 md:p-6">
            {phase === "generating" && !mdReady ? (
              <TypingDots label="Generating SKILL.md\u2026" />
            ) : (
              <motion.div
                key="skill-md-ready"
                initial={prefersReduced ? { opacity: 1 } : { opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                transition={
                  prefersReduced
                    ? fades.instant
                    : { duration: 0.3, ease: "easeOut" }
                }
              >
                <SkillMarkdown content={generatedSkillMd} />
              </motion.div>
            )}
          </PanelTabContent>

          {/* ── Validation ───────────────────────────────────────── */}
          <PanelTabContent value="validation" className="p-4 md:p-6 space-y-5">
            <h3 className="text-foreground" style={typo.h3}>
              Validation Report
            </h3>
            {phase === "validating" ? (
              <TypingDots label="Running compliance checks\u2026" />
            ) : (
              <>
                <Card>
                  <CardContent className="p-4">
                    <SectionHeader
                      icon={<Shield className="text-chart-3" />}
                      className="mb-3"
                    >
                      <span className="text-foreground" style={typo.label}>
                        Compliance Check — Passed
                      </span>
                    </SectionHeader>
                    <div className="space-y-2">
                      {[
                        "File structure valid",
                        "YAML frontmatter present",
                        "Required sections complete",
                        "Naming conventions followed",
                      ].map((item) => (
                        <div key={item} className="flex items-center gap-2">
                          <div className="w-1.5 h-1.5 rounded-full bg-chart-3" />
                          <span
                            className="text-muted-foreground"
                            style={typo.caption}
                          >
                            {item}
                          </span>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>

                <Card>
                  <CardContent className="p-4">
                    <span className="text-foreground" style={typo.label}>
                      Quality Assessment
                    </span>
                    <div className="mt-3 space-y-3">
                      {[
                        { label: "Completeness", score: 94 },
                        { label: "Clarity", score: 91 },
                        { label: "Technical Accuracy", score: 96 },
                      ].map(({ label, score }) => (
                        <div key={label}>
                          <div className="flex items-center justify-between mb-1">
                            <span
                              className="text-muted-foreground"
                              style={typo.caption}
                            >
                              {label}
                            </span>
                            <span
                              className="text-foreground"
                              style={typo.label}
                            >
                              {score}%
                            </span>
                          </div>
                          <Progress
                            value={score}
                            className="h-1.5 [&>[data-slot=progress-indicator]]:bg-accent"
                          />
                        </div>
                      ))}
                    </div>
                    <div className="mt-4 pt-3 border-t border-border-subtle flex items-center justify-between">
                      <span
                        className="text-foreground"
                        style={{
                          ...typo.label,
                          fontWeight: "var(--font-weight-semibold)",
                        }}
                      >
                        Overall Score
                      </span>
                      <span
                        className="text-accent"
                        style={{
                          ...typo.h3,
                          fontWeight: "var(--font-weight-semibold)",
                        }}
                      >
                        94 / 100
                      </span>
                    </div>
                  </CardContent>
                </Card>
              </>
            )}
          </PanelTabContent>
        </PanelTabPanels>
      </ScrollArea>
    </PanelTabs>
  );
}

import { typo } from "@/lib/config/typo";
import { useState } from "react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import { MessageSquare, Pencil, CircleCheck } from "lucide-react";
import type { ChatMessage } from "@/lib/data/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { RadioOptionCard } from "@/components/ui/radio-option-card";
import { SectionHeader } from "@/components/shared/SectionHeader";
import { ResolvedChip } from "@/components/shared/ResolvedChip";

interface Props {
  data: NonNullable<ChatMessage["clarificationData"]>;
  onResolve: (answer: string) => void;
}

export function ClarificationCard({ data, onResolve }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [customText, setCustomText] = useState("");
  const isCustom = selectedId === data.customOptionId;
  const canConfirm =
    selectedId !== null && (!isCustom || customText.trim().length > 0);
  const prefersReduced = useReducedMotion();

  const handleConfirm = () => {
    if (!canConfirm) return;
    const selected = data.options.find((o) => o.id === selectedId);
    const answer = isCustom ? customText.trim() : selected?.label || "";
    onResolve(answer);
  };

  // ── Resolved state ────────────────────────────────────────────
  if (data.resolved) {
    return (
      <motion.div
        initial={{ opacity: 0.8 }}
        animate={{ opacity: 1 }}
        transition={prefersReduced ? { duration: 0.01 } : { duration: 0.2 }}
      >
        <Card>
          <CardContent className="p-4">
            <SectionHeader
              icon={<CircleCheck className="text-chart-3" />}
              className="mb-2"
            >
              <span className="text-muted-foreground" style={typo.helper}>
                {data.stepLabel}
              </span>
            </SectionHeader>
            <p className="text-muted-foreground mb-1" style={typo.caption}>
              {data.question}
            </p>
            <div className="mt-2">
              <ResolvedChip>{data.resolvedAnswer}</ResolvedChip>
            </div>
          </CardContent>
        </Card>
      </motion.div>
    );
  }

  // ── Active state ──────────────────────────────────────────────
  return (
    <motion.div
      initial={{ opacity: 0, y: prefersReduced ? 0 : 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={
        prefersReduced
          ? { duration: 0.01 }
          : { duration: 0.25, ease: [0.25, 0.1, 0.25, 1] }
      }
    >
      <Card className="border-border-subtle bg-card rounded-xl">
        <CardContent className="p-4">
          {/* Header */}
          <SectionHeader
            icon={<MessageSquare className="text-muted-foreground" />}
            className="mb-1"
          >
            <span className="text-muted-foreground" style={typo.helper}>
              {data.stepLabel}
            </span>
          </SectionHeader>
          <p className="text-foreground mb-4" style={typo.label}>
            {data.question}
          </p>

          {/* Options */}
          <div
            className="space-y-2 mb-4"
            role="radiogroup"
            aria-label={data.question}
          >
            {data.options.map((option) => {
              const isSelected = selectedId === option.id;
              const isWriteOwn = option.id === data.customOptionId;

              return (
                <div key={option.id}>
                  <RadioOptionCard
                    selected={isSelected}
                    onSelect={() => {
                      setSelectedId(option.id);
                      if (!isWriteOwn) setCustomText("");
                    }}
                    label={option.label}
                    description={option.description}
                    icon={isWriteOwn ? <Pencil /> : undefined}
                  />

                  {/* Write-your-own text input */}
                  <AnimatePresence>
                    {isWriteOwn && isSelected && (
                      <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: "auto", opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={
                          prefersReduced
                            ? { duration: 0.01 }
                            : { duration: 0.2, ease: "easeOut" }
                        }
                        className="overflow-hidden"
                      >
                        <div className="mt-2 ml-7">
                          <textarea
                            value={customText}
                            onChange={(e) => setCustomText(e.target.value)}
                            placeholder="Describe your specific requirement&#x2026;"
                            rows={2}
                            className="w-full px-3 py-2 rounded-lg border-subtle bg-background text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring focus-visible:border-ring resize-none"
                            style={typo.labelRegular}
                          />
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              );
            })}
          </div>

          {/* Confirm button */}
          <div className="flex justify-end">
            <Button size="sm" disabled={!canConfirm} onClick={handleConfirm}>
              Confirm
            </Button>
          </div>
        </CardContent>
      </Card>
    </motion.div>
  );
}

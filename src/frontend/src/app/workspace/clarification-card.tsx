import { useId, useState } from "react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import { CircleCheck, MessageSquare, Pencil } from "lucide-react";
import type { ChatMessage } from "@/screens/workspace/use-workspace";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Field, FieldLabel } from "@/components/ui/field";
import { RadioOptionCard } from "@/components/ui/radio-option-card";
import { Textarea } from "@/components/ui/textarea";

interface Props {
  data: NonNullable<ChatMessage["clarificationData"]>;
  onResolve: (answer: string) => void;
}

export function ClarificationCard({ data, onResolve }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [customText, setCustomText] = useState("");
  const customAnswerId = useId();
  const isCustom = selectedId === data.customOptionId;
  const canConfirm = selectedId !== null && (!isCustom || customText.trim().length > 0);
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
          <CardContent className="flex flex-col gap-3 p-4">
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <span className="shrink-0 [&_svg]:h-4 [&_svg]:w-4" aria-hidden="true">
                  <CircleCheck className="text-chart-3" />
                </span>
                <span className="text-muted-foreground typo-helper">{data.stepLabel}</span>
              </div>
              <p className="text-muted-foreground typo-caption">{data.question}</p>
            </div>
            <div
              data-slot="resolved-chip"
              className="inline-flex w-fit items-center gap-2 rounded-md bg-muted px-3 py-1.5 text-foreground border-subtle"
            >
              <span data-slot="resolved-chip-label" className="text-sm font-medium leading-5">
                {data.resolvedAnswer}
              </span>
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
        prefersReduced ? { duration: 0.01 } : { duration: 0.25, ease: [0.25, 0.1, 0.25, 1] }
      }
    >
      <Card className="border-border-subtle bg-card rounded-xl">
        <CardContent className="flex flex-col gap-4 p-4">
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <span className="shrink-0 [&_svg]:h-4 [&_svg]:w-4" aria-hidden="true">
                <MessageSquare className="text-muted-foreground" />
              </span>
              <span className="text-muted-foreground typo-helper">{data.stepLabel}</span>
            </div>
            <p className="text-foreground typo-label">{data.question}</p>
          </div>

          <div className="flex flex-col gap-2" role="radiogroup" aria-label={data.question}>
            {data.options.map((option) => {
              const isSelected = selectedId === option.id;
              const isWriteOwn = option.id === data.customOptionId;

              return (
                <div key={option.id} className="flex flex-col gap-2">
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

                  <AnimatePresence>
                    {isWriteOwn && isSelected && (
                      <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: "auto", opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={
                          prefersReduced ? { duration: 0.01 } : { duration: 0.2, ease: "easeOut" }
                        }
                        className="overflow-hidden"
                      >
                        <Field className="ml-7">
                          <FieldLabel className="sr-only" htmlFor={customAnswerId}>
                            Describe your specific requirement
                          </FieldLabel>
                          <Textarea
                            id={customAnswerId}
                            value={customText}
                            onChange={(event) => setCustomText(event.currentTarget.value)}
                            placeholder="Describe your specific requirement&#x2026;"
                            rows={2}
                            className="min-h-16 bg-background"
                          />
                        </Field>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              );
            })}
          </div>

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

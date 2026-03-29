import { useId, useState } from "react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import { CircleCheck, MessageSquare, Pencil } from "lucide-react";
import type { ChatMessage } from "@/screens/workspace/use-workspace";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Field, FieldLabel } from "@/components/ui/field";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";

interface Props {
  data: NonNullable<ChatMessage["clarificationData"]>;
  onResolve: (answer: string) => void;
}

export function ClarificationCard({ data, onResolve }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [customText, setCustomText] = useState("");
  const customAnswerId = useId();
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
          <CardContent className="flex flex-col gap-3 p-4">
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <span
                  className="shrink-0 [&_svg]:h-4 [&_svg]:w-4"
                  aria-hidden="true"
                >
                  <CircleCheck className="text-chart-3" />
                </span>
                <span className="text-muted-foreground typo-helper">
                  {data.stepLabel}
                </span>
              </div>
              <p className="text-muted-foreground typo-caption">
                {data.question}
              </p>
            </div>
            <Badge variant="secondary" className="w-fit px-3 py-1.5 text-sm">
              {data.resolvedAnswer}
            </Badge>
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
        <CardContent className="flex flex-col gap-4 p-4">
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <span
                className="shrink-0 [&_svg]:h-4 [&_svg]:w-4"
                aria-hidden="true"
              >
                <MessageSquare className="text-muted-foreground" />
              </span>
              <span className="text-muted-foreground typo-helper">
                {data.stepLabel}
              </span>
            </div>
            <p className="text-foreground typo-label">{data.question}</p>
          </div>

          <ToggleGroup
            type="single"
            value={selectedId ?? ""}
            onValueChange={(nextValue) => {
              setSelectedId(nextValue || null);
              if (nextValue !== data.customOptionId) {
                setCustomText("");
              }
            }}
            className="w-full flex-col"
            variant="card"
            aria-label={data.question}
          >
            {data.options.map((option) => {
              const isSelected = selectedId === option.id;
              const isWriteOwn = option.id === data.customOptionId;

              return (
                <div key={option.id} className="flex flex-col gap-2">
                  <ToggleGroupItem
                    value={option.id}
                    className="group/clarification-option w-full"
                    aria-label={option.label}
                  >
                    <div
                      className={cn(
                        "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border-2 transition-colors",
                        isSelected
                          ? "border-foreground"
                          : "border-muted-foreground/40",
                      )}
                      aria-hidden="true"
                    >
                      <div
                        className={cn(
                          "size-2.5 rounded-full bg-foreground transition-transform",
                          isSelected ? "scale-100" : "scale-0",
                        )}
                      />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        {isWriteOwn ? (
                          <Pencil
                            className="shrink-0 text-muted-foreground"
                            aria-hidden="true"
                          />
                        ) : null}
                        <span className="text-left text-foreground typo-label">
                          {option.label}
                        </span>
                      </div>
                      {option.description ? (
                        <p className="mt-0.5 text-left text-muted-foreground typo-caption">
                          {option.description}
                        </p>
                      ) : null}
                    </div>
                  </ToggleGroupItem>

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
                        <Field className="ml-7">
                          <FieldLabel
                            className="sr-only"
                            htmlFor={customAnswerId}
                          >
                            Describe your specific requirement
                          </FieldLabel>
                          <Textarea
                            id={customAnswerId}
                            value={customText}
                            onChange={(event) =>
                              setCustomText(event.currentTarget.value)
                            }
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
          </ToggleGroup>

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

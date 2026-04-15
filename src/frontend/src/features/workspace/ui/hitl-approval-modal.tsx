import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ChatMessage } from "@/lib/workspace/workspace-types";

interface HitlApprovalModalProps {
  message: ChatMessage | null;
  onResolveHitl: (msgId: string, actionLabel: string) => void;
}

export function HitlApprovalModal({ message, onResolveHitl }: HitlApprovalModalProps) {
  const prefersReduced = useReducedMotion();
  const isVisible = message != null && !message.hitlData?.resolved;

  return (
    <AnimatePresence>
      {isVisible && message?.hitlData ? (
        <motion.div
          key={message.id}
          initial={{ opacity: 0, y: prefersReduced ? 0 : 16 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: prefersReduced ? 0 : 8 }}
          transition={
            prefersReduced
              ? { duration: 0.01 }
              : { type: "spring", stiffness: 420, damping: 32, mass: 0.8 }
          }
          className="pointer-events-none fixed inset-x-0 bottom-0 z-50 flex items-end justify-center pb-32 px-4"
          aria-live="assertive"
          aria-atomic="true"
        >
          {/* Backdrop */}
          <div className="pointer-events-none absolute inset-0 bg-black/20 backdrop-blur-[2px]" />

          {/* Panel */}
          <div
            className="pointer-events-auto relative w-full max-w-md rounded-2xl border border-border-subtle/70 bg-card/95 shadow-2xl shadow-black/20 backdrop-blur-sm"
            role="dialog"
            aria-modal="true"
            aria-label="Approval required"
          >
            {/* Header */}
            <div className="flex items-center gap-2.5 border-b border-border-subtle/50 px-5 py-3.5">
              <Loader2 className="size-4 shrink-0 animate-spin text-primary" aria-hidden="true" />
              <span className="text-xs font-semibold uppercase tracking-widest text-primary">
                Approval Required
              </span>
            </div>

            {/* Question */}
            <div className="px-5 py-4">
              <p className="text-sm leading-relaxed text-foreground">{message.hitlData.question}</p>
            </div>

            {/* Actions */}
            <div className="flex items-center justify-end gap-2 border-t border-border-subtle/50 px-5 py-3.5">
              {message.hitlData.actions.map((action, index) => (
                <Button
                  key={`${message.id}-modal-action-${index}`}
                  size="sm"
                  variant={action.variant === "primary" ? "default" : "outline"}
                  className={cn(
                    "h-8 px-4 text-sm",
                    action.variant === "primary" &&
                      "border-primary bg-primary text-primary-foreground hover:bg-primary/90",
                  )}
                  onClick={() => onResolveHitl(message.id, action.label)}
                >
                  {action.label}
                </Button>
              ))}
            </div>
          </div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

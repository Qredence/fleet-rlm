import { motion, useReducedMotion } from "motion/react";

import { QredenceLogo } from "@/components/brand-mark";
import { StateNotice } from "@/components/product";
import { cn } from "@/lib/utils";

interface WorkspaceChatEmptyStateHeroProps {
  isMobile: boolean;
}

/** Hero copy for the centered empty chat layout (suggestions are owned by AgentChat). */
export function WorkspaceChatEmptyStateHero({ isMobile }: WorkspaceChatEmptyStateHeroProps) {
  const prefersReduced = useReducedMotion();

  return (
    <motion.div
      initial={{ opacity: 0, y: prefersReduced ? 0 : 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={prefersReduced ? { duration: 0.01 } : { duration: 0.28, ease: "easeOut" }}
      className={cn(
        "flex w-full max-w-3xl flex-col items-center gap-4 pb-6 text-center",
        isMobile ? "pt-6" : "pt-0",
      )}
    >
      <StateNotice
        icon={<QredenceLogo className="size-12 text-accent-foreground" />}
        title="Qredence Fleet"
        description="Type a message below to begin working with the AI assistant"
        className="w-full py-0"
        titleClassName="typo-display-lg font-semibold leading-tight tracking-tighter-custom"
      />
    </motion.div>
  );
}

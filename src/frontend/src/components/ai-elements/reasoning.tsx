import { type ReactNode } from "react";
import {
  Reasoning as BaseReasoning,
  type ReasoningProps as BaseReasoningProps,
} from "@/components/ui/reasoning";

export type ReasoningPart = { type: "text"; text: string };

interface ReasoningProps {
  parts: ReasoningPart[];
  isStreaming?: boolean;
  duration?: number;
  defaultOpen?: boolean;
  className?: string;
}

function Reasoning({
  parts,
  isStreaming = false,
  duration,
  defaultOpen,
  className,
}: ReasoningProps) {
  return (
    <BaseReasoning
      parts={parts}
      isThinking={isStreaming}
      duration={duration}
      defaultOpen={defaultOpen}
      className={className as BaseReasoningProps["className"]}
    />
  );
}

function ReasoningTrigger({ children }: { children?: ReactNode }) {
  return <>{children}</>;
}

function ReasoningContent({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

export { Reasoning, ReasoningTrigger, ReasoningContent };

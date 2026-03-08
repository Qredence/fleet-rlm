import { type ReactNode } from "react";
import {
  Reasoning as BaseReasoning,
  type ReasoningProps as BaseReasoningProps,
} from "@/components/ui/reasoning";
import { Streamdown } from "@/components/ui/streamdown";
import { typo } from "@/lib/config/typo";
import { cn } from "@/lib/utils/cn";
import { Sparkles } from "lucide-react";
export type ReasoningPart = { type: "text"; text: string };
export type ReasoningDensity = "default" | "compact";
export type ReasoningDisplayMode = "collapsible" | "inline_always";
interface ReasoningProps {
  parts: ReasoningPart[];
  isStreaming?: boolean;
  duration?: number;
  defaultOpen?: boolean;
  density?: ReasoningDensity;
  displayMode?: ReasoningDisplayMode;
  className?: string;
}
function Reasoning({
  parts,
  isStreaming = false,
  duration,
  defaultOpen,
  density = "default",
  displayMode = "collapsible",
  className,
}: ReasoningProps) {
  const mergedContent = parts.map((part) => part.text).join("");

  if (displayMode === "inline_always") {
    return (
      <div
        data-slot="reasoning-inline"
        className={cn(
          "w-full rounded-2xl border border-border-subtle/80 bg-card/35 px-3 py-2.5",
          density === "compact" ? "space-y-2" : "space-y-3",
          className,
        )}
      >
        <div className="inline-flex items-center gap-1.5 text-muted-foreground">
          <Sparkles className="size-3 text-muted-foreground/80" aria-hidden />
          <span style={typo.helper}>
            {isStreaming ? "Reasoning in progress" : "Reasoning trace"}
          </span>
        </div>
        <div className="text-muted-foreground" style={typo.base}>
          <Streamdown content={mergedContent} streaming={isStreaming} />
        </div>
      </div>
    );
  }
  return (
    <BaseReasoning
      parts={parts}
      isThinking={isStreaming}
      duration={duration}
      defaultOpen={defaultOpen}
      className={cn(className) as BaseReasoningProps["className"]}
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

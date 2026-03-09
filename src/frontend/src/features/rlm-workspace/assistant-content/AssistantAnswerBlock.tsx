import { MessageResponse } from "@/components/ai-elements/message";
import { Shimmer } from "@/components/ai-elements/shimmer";

function LoadingState() {
  return (
    <div className="flex flex-col gap-2" data-slot="assistant-loading">
      <Shimmer
        as="span"
        className="text-[11px] font-medium uppercase tracking-[0.2em] text-muted-foreground"
      >
        Loading
      </Shimmer>
      <div className="space-y-1.5">
        <div className="h-2.5 w-36 rounded-full bg-muted/70" />
        <div className="h-2.5 w-24 rounded-full bg-muted/45" />
      </div>
    </div>
  );
}

export function AssistantAnswerBlock({
  text,
  showStreamingShell,
}: {
  text: string;
  showStreamingShell: boolean;
}) {
  if (!text && !showStreamingShell) return null;

  return (
    <div className="space-y-1.5" data-slot="assistant-answer">
      {text ? <MessageResponse>{text}</MessageResponse> : <LoadingState />}
    </div>
  );
}

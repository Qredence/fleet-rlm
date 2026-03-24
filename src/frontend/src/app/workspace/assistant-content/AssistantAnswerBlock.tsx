import { MessageResponse } from "@/components/ai-elements/message";
import { TextShimmer } from "@/components/effects/text-shimmer";

function LoadingState() {
  return (
    <div data-slot="assistant-loading" className="flex items-center gap-2 py-1">
      <TextShimmer as="span" className="text-sm text-muted-foreground">
        Thinking...
      </TextShimmer>
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
    <div data-slot="assistant-answer">
      {text ? (
        <MessageResponse>{text}</MessageResponse>
      ) : (
        <LoadingState />
      )}
    </div>
  );
}

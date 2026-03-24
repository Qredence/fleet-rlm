import { MessageResponse } from "@/components/ai-elements/message";
import { TextShimmer } from "@/components/ui/text-shimmer";

function LoadingState() {
  return (
    <div data-slot="assistant-loading">
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
    <div className="space-y-1.5" data-slot="assistant-answer">
      {text ? <MessageResponse>{text}</MessageResponse> : <LoadingState />}
    </div>
  );
}

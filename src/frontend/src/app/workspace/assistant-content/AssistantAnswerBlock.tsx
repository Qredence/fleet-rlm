import { MessageResponse } from "@/components/ai-elements/message";
import { TextShimmer } from "@/components/ui/text-shimmer";

function LoadingState() {
  return (
    <div data-slot="assistant-loading">
      <TextShimmer as="span" className="text-sm text-muted-foreground">
        Generating code...
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
    <div className="flex flex-col gap-1.5" data-slot="assistant-answer">
      {text ? <MessageResponse>{text}</MessageResponse> : <LoadingState />}
    </div>
  );
}

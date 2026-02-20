import { Streamdown } from "@/components/ui/streamdown";

interface AssistantMessageProps {
  content: string;
  streaming?: boolean;
}

/**
 * AssistantMessage — renders a single AI response bubble using the
 * Streamdown progressive-markdown renderer.
 */
export function AssistantMessage({
  content,
  streaming,
}: AssistantMessageProps) {
  return (
    <div className="w-full">
      <Streamdown
        content={content}
        streaming={streaming}
        speed={4}
        interval={16}
      />
    </div>
  );
}

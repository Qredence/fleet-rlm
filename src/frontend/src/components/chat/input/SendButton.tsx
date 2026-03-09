import { ArrowUp } from "lucide-react";

import { PromptInputSubmit } from "@/components/ai-elements/prompt-input";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils/cn";

interface SendButtonProps {
  disabled: boolean;
  isLoading?: boolean;
  isReceiving?: boolean;
}

function SendButton({
  disabled,
  isLoading = false,
  isReceiving = false,
}: SendButtonProps) {
  return (
    <PromptInputSubmit
      aria-label={isLoading ? "Sending message" : "Submit"}
      aria-busy={isReceiving}
      className={cn(
        "prompt-composer-submit-button aspect-square size-[26px] min-h-[26px] min-w-[26px] rounded-full first:rounded-full last:rounded-full",
        "transition-[background-color,color,box-shadow,opacity]",
      )}
      disabled={disabled}
      size="icon"
      variant="ghost"
    >
      {isLoading ? <Spinner size="sm" /> : <ArrowUp className="size-[18px]" />}
    </PromptInputSubmit>
  );
}

export { SendButton };

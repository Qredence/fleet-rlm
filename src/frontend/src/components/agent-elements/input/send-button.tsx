import { IconArrowUp, IconPlayerStopFilled } from "@tabler/icons-react";
import { cn } from "../utils/cn";

export type SendButtonProps = {
  state: "idle" | "typing" | "streaming";
};

export function SendButton({ state }: SendButtonProps) {
  const isStreaming = state === "streaming";
  const isTyping = state === "typing";

  if (isStreaming) {
    return (
      <div className="flex size-6 cursor-pointer items-center justify-center rounded-full bg-foreground">
        <IconPlayerStopFilled className="size-3 text-background" />
      </div>
    );
  }

  return (
    <div
      className={cn(
        "flex size-6 items-center justify-center rounded-full",
        isTyping ? "cursor-pointer bg-an-send-button-bg" : "cursor-default bg-muted",
      )}
    >
      <IconArrowUp
        className={cn(
          "size-3",
          isTyping ? "text-an-send-button-color" : "text-an-input-placeholder-color",
        )}
      />
    </div>
  );
}

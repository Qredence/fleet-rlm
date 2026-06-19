import type { TurnItem } from "@/lib/rlm-api/sessions";
import type { ChatMessage } from "@/lib/workspace/workspace-types";

export function chatMessagesFromTurns(turns: TurnItem[]): ChatMessage[] {
  return turns.flatMap((turn) => {
    const baseId = `turn-${turn.id || turn.turn_index}`;
    const messages: ChatMessage[] = [
      {
        id: `${baseId}-user`,
        type: "user",
        content: turn.user_message,
      },
    ];

    if (turn.assistant_message?.trim()) {
      messages.push({
        id: `${baseId}-assistant`,
        type: "assistant",
        content: turn.assistant_message,
        streaming: false,
      });
    }

    return messages;
  });
}

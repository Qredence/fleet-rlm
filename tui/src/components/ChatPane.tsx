/**
 * ChatPane - scrollable conversation transcript with polished styling.
 * Surface background, role badges, and styled message bubbles.
 */

import { useAppContext } from "../context/AppContext";
import { bg, border, fg, accent, semantic } from "../theme";
import type { TranscriptEvent } from "../types/protocol";

function MessageBubble({ event }: { event: TranscriptEvent }) {
  if (event.role === "user") {
    return (
      <box
        width="100%"
        paddingTop={1}
        paddingBottom={1}
        paddingLeft={2}
        paddingRight={2}
        backgroundColor={bg.highlight}
      >
        <text>
          <span fg={accent.base}>{" > "}</span>
          <span fg={fg.primary}>{event.content}</span>
        </text>
      </box>
    );
  }

  if (event.role === "assistant") {
    return (
      <box
        width="100%"
        paddingTop={1}
        paddingBottom={1}
        paddingLeft={2}
        paddingRight={2}
      >
        <text>
          <span fg={fg.muted}>{" ◆ "}</span>
          <span fg={fg.primary}>{event.content}</span>
        </text>
      </box>
    );
  }

  if (event.role === "system" || event.role === "status") {
    return (
      <box
        width="100%"
        paddingTop={1}
        paddingBottom={1}
        paddingLeft={2}
        paddingRight={2}
      >
        <text fg={fg.secondary}>{event.content}</text>
      </box>
    );
  }

  if (event.role === "trace") {
    return (
      <box
        width="100%"
        paddingTop={1}
        paddingBottom={1}
        paddingLeft={2}
        paddingRight={2}
      >
        <text fg={fg.secondary}>
          <span fg={accent.dim}>{" ⟳ "}</span>
          {event.content}
        </text>
      </box>
    );
  }

  return null;
}

export function ChatPane() {
  const { state } = useAppContext();

  const messages: TranscriptEvent[] = [...state.transcript];

  if (state.isProcessing && state.currentTurn.transcriptText) {
    messages.push({
      role: "assistant",
      content: state.currentTurn.transcriptText + "▊",
    });
  }

  if (state.currentTurn.errored && state.currentTurn.errorMessage) {
    messages.push({
      role: "system",
      content: `Error: ${state.currentTurn.errorMessage}`,
    });
  }

  if (messages.length === 0) {
    return (
      <box
        flexGrow={1}
        backgroundColor={bg.surface}
        border
        borderStyle="rounded"
        borderColor={border.dim}
        title=" Chat "
        titleAlignment="center"
        alignItems="center"
        justifyContent="center"
      >
        <text fg={fg.muted}>Type a message to start chatting...</text>
      </box>
    );
  }

  return (
    <box
      flexGrow={1}
      backgroundColor={bg.surface}
      border
      borderStyle="rounded"
      borderColor={border.dim}
      title=" Chat "
      titleAlignment="center"
    >
      <scrollbox
        flexGrow={1}
        focused
        padding={1}
      >
        {messages.map((event, i) => (
          <MessageBubble key={i} event={event} />
        ))}
      </scrollbox>
    </box>
  );
}

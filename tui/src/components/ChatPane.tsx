/**
 * ChatPane - scrollable conversation transcript with polished styling.
 * Surface background, role badges, and styled message bubbles.
 */

import { useRef, useEffect, useState, useCallback } from "react";
import { useAppContext } from "../context/AppContext";
import { useKeyboard } from "@opentui/react";
import { bg, border, fg, accent, semantic } from "../theme";
import type { TranscriptEvent } from "../types/protocol";
import { Spinner } from "./Spinner";
import { parseMarkdown, hasMarkdown } from "../utils/markdown";
import { copyToClipboard } from "../hooks/useClipboard";

function MessageBubble({ event }: { event: TranscriptEvent }) {
  const isError = event.role === "system" && event.content.startsWith("Error:");
  const hasMd = event.role === "assistant" && hasMarkdown(event.content);

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
        flexDirection="column"
      >
        <text>
          <span fg={fg.muted}>{" ◆ "}</span>
        </text>
        {hasMd ? (
          <box paddingLeft={3} flexDirection="column">
            {parseMarkdown({ content: event.content, baseColor: fg.primary, accentColor: accent.base })}
          </box>
        ) : (
          <text paddingLeft={3}>
            <span fg={fg.primary}>{event.content}</span>
          </text>
        )}
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
        <text fg={isError ? semantic.error : fg.secondary}>{event.content}</text>
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
  const scrollRef = useRef<any>(null);
  const prevMessageCount = useRef(0);
  const [copiedFeedback, setCopiedFeedback] = useState(false);

  const messages: TranscriptEvent[] = [...state.transcript];

  const isProcessing = state.isProcessing;
  const hasNewMessage = messages.length > prevMessageCount.current;
  prevMessageCount.current = messages.length;

  // Copy last assistant message to clipboard
  const copyLastMessage = useCallback(() => {
    // Find last assistant message
    let lastAssistantContent: string | null = null;
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i]?.role === "assistant") {
        lastAssistantContent = messages[i]?.content || null;
        break;
      }
    }

    if (lastAssistantContent) {
      const success = copyToClipboard(lastAssistantContent);
      if (success) {
        setCopiedFeedback(true);
        setTimeout(() => setCopiedFeedback(false), 1500);
      }
    }
  }, [messages]);

  // Handle Ctrl+Y to copy
  useKeyboard((key) => {
    if (key.ctrl && key.name === "y") {
      copyLastMessage();
    }
  });

  if (isProcessing && state.currentTurn.transcriptText) {
    messages.push({
      role: "assistant",
      content: state.currentTurn.transcriptText,
    });
  }

  if (state.currentTurn.errored && state.currentTurn.errorMessage) {
    messages.push({
      role: "system",
      content: `Error: ${state.currentTurn.errorMessage}`,
    });
  }

  useEffect(() => {
    if (hasNewMessage && scrollRef.current?.scrollToBottom) {
      scrollRef.current.scrollToBottom();
    }
  }, [messages.length, hasNewMessage]);

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
      flexDirection="column"
      backgroundColor={bg.surface}
      border
      borderStyle="rounded"
      borderColor={border.dim}
      title=" Chat "
      titleAlignment="center"
    >
      {copiedFeedback && (
        <box height={1} backgroundColor={semantic.success} paddingLeft={2}>
          <text fg="#000000">Copied to clipboard!</text>
        </box>
      )}
      <scrollbox
        ref={scrollRef}
        flexGrow={1}
        focused
        padding={1}
      >
        {messages.map((event, i) => (
          <MessageBubble key={i} event={event} />
        ))}
        {isProcessing && (
          <box paddingTop={1} paddingBottom={1} paddingLeft={2}>
            <Spinner name="dots" interval={80} color={accent.base} />
          </box>
        )}
      </scrollbox>
    </box>
  );
}

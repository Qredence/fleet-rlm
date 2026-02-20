/**
 * ChatPane - scrollable conversation transcript with polished styling.
 * Surface background, role badges, and styled message bubbles.
 * Supports copying last assistant message with Ctrl+C.
 */

import { useRef, useEffect, useState, memo, useCallback } from "react";
import { useAppContext } from "../context/AppContext";
import { useRegisterKeyHandler, PRIORITY } from "../context/KeyboardContext";
import { bg, border, fg, accent, semantic } from "../theme";
import type { TranscriptEvent } from "../types/protocol";
import { Spinner } from "./Spinner";
import { parseMarkdown, hasMarkdown } from "../utils/markdown";
import { copyToClipboard } from "../hooks/useClipboard";

function MessageBubbleInner({ event }: { event: TranscriptEvent }) {
  const isError = event.role === "system" && event.content.startsWith("Error:");
  const hasMd = event.role === "assistant" && hasMarkdown(event.content);
  const displayTime = event.timestamp ? new Date(event.timestamp) : null;
  const timeStr = displayTime ? formatTimeAgo(displayTime) : null;

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
          <span fg={fg.muted}>{`◆ `}</span>
          {timeStr && <span fg={fg.muted}>{timeStr}</span>}
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

const MessageBubble = memo(MessageBubbleInner);

interface ChatPaneProps {
  focused?: boolean;
  onFocus?: () => void;
}

export function ChatPane({ focused = false, onFocus }: ChatPaneProps) {
  const { state } = useAppContext();
  const scrollRef = useRef<any>(null);
  const prevMessageCount = useRef(0);
  const [elapsedTime, setElapsedTime] = useState(0);
  const elapsedInterval = useRef<NodeJS.Timeout | null>(null);

  const messages: TranscriptEvent[] = [...state.transcript];

  const isProcessing = state.isProcessing;
  const processingStartTime = state.processingStartTime;
  const hasNewMessage = messages.length > prevMessageCount.current;
  prevMessageCount.current = messages.length;

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
    if (isProcessing && processingStartTime) {
      elapsedInterval.current = setInterval(() => {
        setElapsedTime(Math.floor((Date.now() - processingStartTime) / 1000));
      }, 1000);
    } else {
      if (elapsedInterval.current) {
        clearInterval(elapsedInterval.current);
      }
      setElapsedTime(0);
    }
    return () => {
      if (elapsedInterval.current) {
        clearInterval(elapsedInterval.current);
      }
    };
  }, [isProcessing, processingStartTime]);

  useEffect(() => {
    if (hasNewMessage && scrollRef.current?.scrollToBottom) {
      scrollRef.current.scrollToBottom();
    }
  }, [messages.length, hasNewMessage]);

  // Copy handler - copy last assistant message when focused and Ctrl+C pressed
  const handleCopy = useCallback((key: { ctrl: boolean; name: string }) => {
    if (!focused) return false;

    if (key.ctrl && key.name === "c") {
      const lastAssistant = [...messages].reverse().find(m => m.role === "assistant");
      if (lastAssistant) {
        copyToClipboard(lastAssistant.content);
        // Could show toast here if we had access to toast context
        return true;
      }
    }

    return false;
  }, [focused, messages]);

  useRegisterKeyHandler("chatCopy", handleCopy, PRIORITY.PANE);

  if (messages.length === 0) {
    return (
      <box
        flexGrow={1}
        backgroundColor={bg.surface}
        border
        borderStyle="rounded"
        borderColor={focused ? accent.base : border.dim}
        title=" Chat "
        titleAlignment="center"
        alignItems="center"
        justifyContent="center"
        onMouseDown={onFocus}
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
      borderColor={focused ? accent.base : border.dim}
      title=" Chat "
      titleAlignment="center"
      onMouseDown={onFocus}
    >
      <scrollbox
        ref={scrollRef}
        flexGrow={1}
        focused
        padding={1}
        style={{
          scrollbarOptions: {
            showArrows: true,
            trackOptions: {
              foregroundColor: accent.base,
              backgroundColor: bg.highlight,
            },
          },
        }}
      >
        {messages.map((event, i) => (
          <MessageBubble key={i} event={event} />
        ))}
        {isProcessing && (
          <box paddingTop={1} paddingBottom={1} paddingLeft={2}>
            <Spinner name="dots" interval={80} color={accent.base} />
            <text fg={fg.secondary} paddingLeft={1}>
              Processing... {state.currentTurn.tokenCount} tokens
              {elapsedTime > 0 && ` \u2022 ${formatElapsed(elapsedTime)}`}
            </text>
          </box>
        )}
      </scrollbox>
    </box>
  );
}

function formatTimeAgo(date: Date): string {
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHour = Math.floor(diffMin / 60);

  if (diffSec < 60) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffHour < 24) return `${diffHour}h ago`;
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatElapsed(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

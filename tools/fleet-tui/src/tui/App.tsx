/** Root Ink app: chat thread + prompt + status bar. */

import { Box, Text, useApp, useInput, useWindowSize } from "ink";
import { useCallback, useEffect, useMemo, useRef, useState, type FC } from "react";

import type { FleetApiClient, FleetSession } from "../fleet-api-client.js";
import { parseInput, type CommandContext } from "./commands.js";
import { Prompt, PromptHelp } from "./prompt.js";
import { StatusBar } from "./status-bar.js";
import { ConversationStore, useConversationStore, listToggleableMessageIds, type Message, type StoreEvent } from "./store.js";
import { RunController } from "./runner.js";
import {
  Artifact,
  Attachment,
  ChatMessage,
  Code,
  Output,
  Reasoning,
  Result,
  Skill,
  ToolCall,
  Usage,
  Warning,
} from "./views/index.js";
import { theme } from "./theme.js";
import {
  initialTimelineScroll,
  reduceTimelineScroll,
  TimelineViewport,
} from "./timeline.js";

export type AppProps = {
  apiUrl: string;
  client: FleetApiClient;
  session: FleetSession;
  resumed: boolean;
  initialEvents: StoreEvent[];
};

export const App: FC<AppProps> = ({ client, session, resumed, initialEvents }) => {
  const { exit } = useApp();
  const { columns: width, rows: height } = useWindowSize();
  const storeRef = useRef<ConversationStore | null>(null);
  const controllerRef = useRef<RunController | null>(null);
  if (!storeRef.current) {
    storeRef.current = new ConversationStore();
  }
  if (!controllerRef.current) {
    controllerRef.current = new RunController(storeRef.current, client);
  }
  const store = storeRef.current;
  const controller = controllerRef.current;
  const state = useConversationStore(store);
  const [timelineScroll, setTimelineScroll] = useState(initialTimelineScroll);
  const [inputFocus, setInputFocus] = useState<"prompt" | "thread">("prompt");
  const inputHeight = 5;
  const threadHeight = Math.max(6, height - inputHeight - 4);

  useEffect(() => {
    setTimelineScroll((scroll) => reduceTimelineScroll(scroll, { type: "reset" }));
    store.dispatch({
      type: "session/hydrate",
      session: {
        id: session.id,
        title: session.title,
        status: session.status,
        resumed,
      },
      events: initialEvents,
    });
  }, [session.id, session.title, session.status, resumed, initialEvents, store]);

  const ctx: CommandContext = useMemo(
    () => ({
      store,
      client,
      cancelActiveRun: () => controller.cancel(),
      exit,
    }),
    [store, client, controller, exit],
  );

  useInput((input, key) => {
    if (key.ctrl && input === "d") {
      exit();
      return;
    }
    if (key.pageUp) {
      setTimelineScroll((scroll) =>
        reduceTimelineScroll(scroll, { type: "page-up", viewportHeight: threadHeight }),
      );
      return;
    }
    if (key.pageDown) {
      setTimelineScroll((scroll) =>
        reduceTimelineScroll(scroll, { type: "page-down", viewportHeight: threadHeight }),
      );
      return;
    }
    if (key.end) {
      setTimelineScroll((scroll) => reduceTimelineScroll(scroll, { type: "end" }));
      return;
    }
    if (key.tab) {
      const toggleableIds = listToggleableMessageIds(state.messages);
      if (toggleableIds.length === 0) {
        setInputFocus("prompt");
        return;
      }
      setInputFocus((focus) => (focus === "prompt" ? "thread" : "prompt"));
    }
  });

  useInput(
    (input, key) => {
      const toggleableIds = listToggleableMessageIds(state.messages);
      if (toggleableIds.length === 0) {
        return;
      }

      if (key.upArrow || input === "k") {
        const current = state.selectedId;
        const index = current ? toggleableIds.indexOf(current) : -1;
        const nextIndex = index <= 0 ? toggleableIds.length - 1 : index - 1;
        const nextId = toggleableIds[nextIndex];
        if (nextId) store.dispatch({ type: "focus/set", id: nextId });
        return;
      }
      if (key.downArrow || input === "j") {
        const current = state.selectedId;
        const index = current ? toggleableIds.indexOf(current) : -1;
        const nextIndex = index < 0 || index >= toggleableIds.length - 1 ? 0 : index + 1;
        const nextId = toggleableIds[nextIndex];
        if (nextId) store.dispatch({ type: "focus/set", id: nextId });
        return;
      }
      if (key.return || input === " ") {
        const targetId = state.selectedId ?? toggleableIds[toggleableIds.length - 1];
        if (targetId) {
          store.dispatch({ type: "message/toggle-expanded", id: targetId });
        }
      }
    },
    { isActive: inputFocus === "thread" },
  );

  const busy = state.run.phase === "submitting" || state.run.phase === "running" || state.run.phase === "cancelling";
  const onTimelineMetrics = useCallback(
    (contentHeight: number, viewportHeight: number) =>
      setTimelineScroll((scroll) =>
        reduceTimelineScroll(scroll, { type: "metrics", contentHeight, viewportHeight }),
      ),
    [],
  );

  const promptTokens = state.messages
    .filter((m): m is Extract<Message, { kind: "usage" }> => m.kind === "usage")
    .reduce((sum, m) => sum + m.prompt, 0);
  const completionTokens = state.messages
    .filter((m): m is Extract<Message, { kind: "usage" }> => m.kind === "usage")
    .reduce((sum, m) => sum + m.completion, 0);

  const onSubmit = (text: string) => {
    const parsed = parseInput(text);
    if (parsed.kind === "empty") return;
    if (parsed.kind === "command") {
      void parsed.spec.handler(parsed.args, ctx);
      return;
    }
    controller.start(parsed.text);
  };

  const onCancel = () => {
    if (busy) {
      controller.cancel();
    } else {
      exit();
    }
  };

  const rowsBehind = timelineScroll.maxScroll - timelineScroll.scrollTop;
  const activeSession = state.session ?? {
    id: session.id,
    title: session.title,
    status: session.status,
    resumed,
  };

  return (
    <Box flexDirection="column" height={height}>
      <Box flexDirection="column" marginBottom={1}>
        <Text color={theme.paper} bold>FLEET</Text>
        <Text color={theme.muted}>{`session ${activeSession.id.slice(0, 8)}… · ${activeSession.resumed ? "resumed" : "new"}${rowsBehind ? ` · ${rowsBehind} rows behind` : " · live"}`}</Text>
      </Box>
      <TimelineViewport
        height={threadHeight}
        scroll={timelineScroll}
        onMetrics={onTimelineMetrics}
      >
        {state.messages.map((message) => (
          <MessageView
            key={message.id}
            message={message}
            width={width}
            store={store}
            focused={state.selectedId === message.id}
          />
        ))}
        {state.messages.length === 0 ? <Text dimColor>{"(empty conversation — type a prompt or /help)"}</Text> : null}
      </TimelineViewport>
      <Box borderStyle="single" borderColor={inputFocus === "prompt" ? theme.paper : theme.rule} paddingX={1} marginTop={1}>
        <Prompt busy={busy} active={inputFocus === "prompt"} onSubmit={onSubmit} onCancel={onCancel} />
      </Box>
      <Box paddingX={1}>
        <PromptHelp busy={busy} active={inputFocus === "prompt"} />
      </Box>
      <StatusBar
        session={state.session}
        run={state.run}
        promptTokens={promptTokens}
        completionTokens={completionTokens}
        width={width}
      />
    </Box>
  );
};

const MessageView: FC<{
  message: Message;
  width: number;
  store: ConversationStore;
  focused: boolean;
}> = ({ message, width, store, focused }) => {
  const expanded = state_isExpanded(store, message.id);
  switch (message.kind) {
    case "text":
      return <ChatMessage message={message} width={width} />;
    case "reasoning":
      return (
        <Reasoning
          message={message}
          width={width}
          expanded={expanded}
          focused={focused}
        />
      );
    case "tool":
      return (
        <ToolCall message={message} expanded={expanded} focused={focused} />
      );
    case "code":
      return (
        <Code
          message={message}
          width={width}
          expanded={expanded}
          focused={focused}
        />
      );
    case "output":
      return <Output message={message} width={width} expanded={expanded} focused={focused} />;
    case "result":
      return <Result message={message} width={width} expanded={expanded} focused={focused} />;
    case "skill":
      return <Skill message={message} />;
    case "attachment":
      return <Attachment message={message} />;
    case "artifact":
      return <Artifact message={message} />;
    case "usage":
      return <Usage message={message} />;
    case "warning":
      return <Warning message={message} />;
    case "error":
      return <ChatMessage message={{ ...message, kind: "text", role: "system", text: message.text, streaming: false }} width={width} />;
  }
};

function state_isExpanded(store: ConversationStore, id: string): boolean {
  return store.getState().expandedIds.has(id);
}

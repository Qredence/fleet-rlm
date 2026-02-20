import { Box, Text, render, useApp, useInput } from "ink";
import type React from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { BridgeClient, type BridgeEvent } from "./bridge.js";
import {
  parseBridgeChatSubmit,
  parseBridgeMentionSearch,
  parseBridgeSessionInit,
  parseBridgeSettingsSnapshot,
  parseBridgeStatusPayload,
} from "./bridge-schemas.js";
import { MentionDebounceController } from "./mention-debounce.js";
import { Composer } from "./components/Composer.js";
import { HeaderBar } from "./components/HeaderBar.js";
import { MentionPanel } from "./components/MentionPanel.js";
import { PalettePanel } from "./components/PalettePanel.js";
import { SettingsEditor } from "./components/SettingsEditor.js";
import { ShellFrame } from "./components/ShellFrame.js";
import { StatusLine } from "./components/StatusLine.js";
import { StatusPanel } from "./components/StatusPanel.js";
import { TranscriptLive } from "./components/TranscriptLive.js";
import { TranscriptStatic } from "./components/TranscriptStatic.js";
import {
  type EventFeedMode,
  clearEventFeed,
  initialEventFeedState,
  reduceInlineEvent,
} from "./event-feed.js";
import {
  type PaletteItem,
  applyMentionSelection,
  buildRootPalette,
  buildSettingsPalette,
  clampIndex,
  detectMentionQuery,
  filterPalette,
  moveIndex,
  parseCommandInput,
} from "./palette.js";
import type {
  CliOptions,
  MentionItem,
  OverlayView,
  SettingsSnapshot,
  TranscriptLine,
  WorkingPhase,
} from "./types.js";

const PLACEHOLDER =
  "Type @ to mention files, / for commands, or ? for shortcuts";
const MENTION_DEBOUNCE_MS = 120;
const DEFAULT_COMMANDS = [
  "help",
  "status",
  "settings",
  "commands",
  "run-long-context",
  "check-secret",
  "check-secret-key",
];

function parseCliOptions(argv: string[]): CliOptions {
  let pythonBin = process.env.PYTHON || "python";
  let traceMode: CliOptions["traceMode"] = "verbose";
  let docsPath: string | undefined;
  let volumeName: string | undefined;
  let secretName: string | undefined;

  const rawHydra: string[] = [];
  let inHydra = false;
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (inHydra) {
      rawHydra.push(token);
      continue;
    }
    if (token === "--") {
      inHydra = true;
      continue;
    }
    if (token === "--bridge-python" && argv[index + 1]) {
      pythonBin = argv[index + 1];
      index += 1;
      continue;
    }
    if (token === "--trace-mode" && argv[index + 1]) {
      const nextMode = argv[index + 1];
      if (
        nextMode === "compact" ||
        nextMode === "verbose" ||
        nextMode === "off"
      ) {
        traceMode = nextMode;
      }
      index += 1;
      continue;
    }
    if (token === "--docs-path" && argv[index + 1]) {
      docsPath = argv[index + 1];
      index += 1;
      continue;
    }
    if (token === "--volume-name" && argv[index + 1]) {
      volumeName = argv[index + 1];
      index += 1;
      continue;
    }
    if (token === "--secret-name" && argv[index + 1]) {
      secretName = argv[index + 1];
      index += 1;
    }
  }

  return {
    pythonBin,
    traceMode,
    docsPath,
    volumeName,
    secretName,
    hydraOverrides: rawHydra,
  };
}

function formatValue(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function suppressKnownInkWarnings(): void {
  const originalError = console.error;
  console.error = (...args: unknown[]) => {
    const first = args[0];
    if (
      typeof first === "string" &&
      first.includes('Each child in a list should have a unique "key" prop')
    ) {
      const hasStaticHint = args.some(
        (item) =>
          typeof item === "string" &&
          item.includes("render method of `Static`"),
      );
      if (hasStaticHint) {
        return;
      }
    }
    originalError(...args);
  };
}

function nextLine(role: TranscriptLine["role"], text: string): TranscriptLine {
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    role,
    text,
  };
}

function FleetInkApp({ options }: { options: CliOptions }): React.JSX.Element {
  const { exit } = useApp();
  const [composerValue, setComposerValue] = useState("");
  const [status, setStatus] = useState("Connecting bridge...");
  const [sessionId, setSessionId] = useState("");
  const [workingPhase, setWorkingPhase] = useState<WorkingPhase>("idle");
  const [transcriptLines, setTranscriptLines] = useState<TranscriptLine[]>([
    nextLine("system", "Welcome to fleet."),
    nextLine(
      "system",
      "Use / for command palette, @ for file mentions, Esc for back.",
    ),
  ]);
  const [streamingText, setStreamingText] = useState("");
  const [commands, setCommands] = useState<string[]>(DEFAULT_COMMANDS);
  const [eventFeedMode, setEventFeedMode] = useState<EventFeedMode>("verbose");
  const [, setEventFeedState] = useState(initialEventFeedState);
  const eventFeedStateRef = useRef(initialEventFeedState());
  const [overlayStack, setOverlayStack] = useState<OverlayView[]>([]);
  const [paletteIndex, setPaletteIndex] = useState(0);
  const [mentionItems, setMentionItems] = useState<MentionItem[]>([]);
  const [mentionIndex, setMentionIndex] = useState(0);
  const [settingsSnapshot, setSettingsSnapshot] = useState<SettingsSnapshot>({
    values: {},
    masked_values: {},
  });
  const [statusPayload, setStatusPayload] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [settingsEditKey, setSettingsEditKey] = useState<
    "DSPY_LM_MODEL" | "DSPY_LM_API_BASE"
  >("DSPY_LM_MODEL");
  const [settingsEditValue, setSettingsEditValue] = useState("");

  const overlayView = overlayStack.at(-1) ?? "none";
  const mentionQuery = useMemo(
    () => (overlayView === "none" ? detectMentionQuery(composerValue) : null),
    [composerValue, overlayView],
  );
  const rootQuery = composerValue.startsWith("/")
    ? composerValue.slice(1).trim()
    : "";

  const rootItems = useMemo(
    () => filterPalette(buildRootPalette(commands), rootQuery),
    [commands, rootQuery],
  );
  const settingsItems = useMemo(
    () => buildSettingsPalette(settingsSnapshot),
    [settingsSnapshot],
  );
  const mentionSuggestions = useMemo(
    () => mentionItems.slice(0, 16),
    [mentionItems],
  );

  const client = useMemo(
    () =>
      new BridgeClient({
        pythonBin: options.pythonBin,
        traceMode: options.traceMode,
        docsPath: options.docsPath,
        volumeName: options.volumeName,
        secretName: options.secretName,
        hydraOverrides: options.hydraOverrides,
      }),
    [
      options.docsPath,
      options.hydraOverrides,
      options.pythonBin,
      options.secretName,
      options.traceMode,
      options.volumeName,
    ],
  );

  const streamingRef = useRef("");
  const pendingFinalTextRef = useRef("");
  const turnInFlightRef = useRef(false);
  const mentionDebounceRef = useRef(
    new MentionDebounceController(MENTION_DEBOUNCE_MS),
  );
  useEffect(() => {
    streamingRef.current = streamingText;
  }, [streamingText]);

  const appendLine = useCallback(
    (role: TranscriptLine["role"], text: string): void => {
      setTranscriptLines((previous) =>
        [...previous, nextLine(role, text)].slice(-400),
      );
    },
    [],
  );
  const appendEvent = useCallback(
    (kind: string, text: string, payload: unknown): void => {
      const next = reduceInlineEvent(eventFeedStateRef.current, eventFeedMode, {
        kind,
        text,
        payload,
      });
      eventFeedStateRef.current = next.state;
      setEventFeedState(next.state);
      if (next.line) {
        appendLine("status", next.line);
      }
    },
    [eventFeedMode, appendLine],
  );

  const clearInlineEventFeed = useCallback((): void => {
    const cleared = clearEventFeed(eventFeedStateRef.current);
    eventFeedStateRef.current = cleared;
    setEventFeedState(cleared);
  }, []);

  const openOverlay = useCallback((view: OverlayView) => {
    if (view === "none") {
      setOverlayStack([]);
      return;
    }
    setOverlayStack((previous) => {
      if (previous.at(-1) === view) {
        return previous;
      }
      return [...previous, view];
    });
    setPaletteIndex(0);
  }, []);

  const goToRootPalette = useCallback(() => {
    setOverlayStack(["palette-root"]);
    setPaletteIndex(0);
  }, []);

  const goToSettingsPalette = useCallback(() => {
    setOverlayStack(["palette-root", "palette-settings"]);
    setPaletteIndex(0);
  }, []);

  const closeOneOverlay = useCallback(() => {
    setOverlayStack((previous) => {
      if (previous.length <= 1) {
        return [];
      }
      return previous.slice(0, -1);
    });
    setPaletteIndex(0);
  }, []);

  const fetchSettings = useCallback(async (): Promise<void> => {
    const response = parseBridgeSettingsSnapshot(
      await client.request("settings.get", {}),
    );
    setSettingsSnapshot(response);
  }, [client]);

  const fetchStatus = useCallback(async (): Promise<void> => {
    const response = parseBridgeStatusPayload(
      await client.request("status.get", {}),
    );
    setStatusPayload(response);
  }, [client]);

  const executeBridgeCommand = useCallback(
    async (command: string, args: Record<string, unknown>): Promise<void> => {
      setWorkingPhase("thinking");
      const result = await client.request("commands.execute", {
        command,
        args,
      });
      appendLine("status", formatValue(result));
      setWorkingPhase("idle");
    },
    [appendLine, client],
  );

  const handleRootSelection = useCallback(
    async (item: PaletteItem): Promise<void> => {
      if (item.nextView === "palette-settings") {
        await fetchSettings();
        goToSettingsPalette();
        setComposerValue("");
        return;
      }
      if (item.action === "show-status") {
        await fetchStatus();
        setOverlayStack(["palette-root", "status-panel"]);
        setPaletteIndex(0);
        setComposerValue("");
        return;
      }
      if (item.action === "list-commands") {
        appendLine("status", `Commands: ${commands.join(", ")}`);
        setOverlayStack([]);
        setComposerValue("");
        return;
      }
      if (item.command) {
        setComposerValue(`/${item.command} `);
        setOverlayStack([]);
        return;
      }
    },
    [appendLine, commands, fetchSettings, fetchStatus, goToSettingsPalette],
  );

  const handleSettingsSelection = useCallback(
    async (item: PaletteItem): Promise<void> => {
      if (item.action === "view-model-provider") {
        const model = settingsSnapshot.masked_values.DSPY_LM_MODEL || "<unset>";
        const apiBase =
          settingsSnapshot.masked_values.DSPY_LM_API_BASE || "<unset>";
        appendLine("status", `Model: ${model}`);
        appendLine("status", `API base: ${apiBase}`);
        return;
      }
      if (item.action === "edit-model") {
        setSettingsEditKey("DSPY_LM_MODEL");
        setSettingsEditValue(settingsSnapshot.values.DSPY_LM_MODEL ?? "");
        openOverlay("settings-edit");
        return;
      }
      if (item.action === "edit-api-base") {
        setSettingsEditKey("DSPY_LM_API_BASE");
        setSettingsEditValue(settingsSnapshot.values.DSPY_LM_API_BASE ?? "");
        openOverlay("settings-edit");
      }
    },
    [
      appendLine,
      openOverlay,
      settingsSnapshot.masked_values,
      settingsSnapshot.values,
    ],
  );

  const applyActiveSelection = useCallback(async (): Promise<void> => {
    if (overlayView === "palette-root") {
      const selected = rootItems[clampIndex(paletteIndex, rootItems.length)];
      if (selected) {
        await handleRootSelection(selected);
      }
      return;
    }
    if (overlayView === "palette-settings") {
      const selected =
        settingsItems[clampIndex(paletteIndex, settingsItems.length)];
      if (selected) {
        await handleSettingsSelection(selected);
      }
      return;
    }
    if (overlayView === "none" && mentionSuggestions.length > 0) {
      const selected =
        mentionSuggestions[clampIndex(mentionIndex, mentionSuggestions.length)];
      if (!selected) {
        return;
      }
      setComposerValue((previous) =>
        applyMentionSelection(previous, selected.path),
      );
    }
  }, [
    handleRootSelection,
    handleSettingsSelection,
    mentionIndex,
    mentionSuggestions,
    overlayView,
    paletteIndex,
    rootItems,
    settingsItems,
  ]);

  const runSlashCommand = useCallback(
    async (line: string): Promise<void> => {
      const { command, args } = parseCommandInput(line);
      if (!command) {
        return;
      }

      if (command === "help") {
        appendLine(
          "status",
          "Shortcuts: / palette, @ mentions, Esc back, Ctrl+L clear transcript, Ctrl+C exit, /events off|compact|verbose|clear",
        );
        return;
      }
      if (command === "events") {
        const raw = String(args.input ?? "")
          .trim()
          .toLowerCase();
        if (raw === "clear") {
          clearInlineEventFeed();
          appendLine("status", "Inline event counters cleared.");
          return;
        }
        if (raw === "off" || raw === "compact" || raw === "verbose") {
          setEventFeedMode(raw);
          appendLine("status", `Inline event mode: ${raw}`);
          return;
        }
        if (!raw || raw === "on") {
          setEventFeedMode("compact");
          appendLine("status", "Inline event mode: compact");
          return;
        }
        appendLine("error", "Usage: /events off|compact|verbose|clear");
        return;
      }
      if (command === "commands") {
        appendLine("status", `Commands: ${commands.join(", ")}`);
        return;
      }
      if (command === "status") {
        await fetchStatus();
        setOverlayStack(["palette-root", "status-panel"]);
        setPaletteIndex(0);
        return;
      }
      if (command === "settings") {
        await fetchSettings();
        goToSettingsPalette();
        return;
      }
      if (command === "model") {
        const model = String(args.input ?? "").trim();
        if (!model) {
          appendLine("error", "Usage: /model <provider/model>");
          return;
        }
        await client.request("settings.update", {
          updates: { DSPY_LM_MODEL: model },
        });
        appendLine("status", `Updated DSPY_LM_MODEL=${model}`);
        await fetchSettings();
        return;
      }

      await executeBridgeCommand(command, args);
    },
    [
      appendLine,
      client,
      clearInlineEventFeed,
      commands,
      executeBridgeCommand,
      fetchSettings,
      fetchStatus,
      goToSettingsPalette,
    ],
  );

  const submitComposerLine = useCallback(
    async (line: string): Promise<void> => {
      const trimmed = line.trim();
      if (!trimmed) {
        return;
      }
      appendLine("user", trimmed);
      setComposerValue("");

      try {
        if (trimmed.startsWith("/")) {
          await runSlashCommand(trimmed);
          return;
        }
        setWorkingPhase("thinking");
        setStreamingText("");
        turnInFlightRef.current = true;
        pendingFinalTextRef.current = "";

        const response = parseBridgeChatSubmit(
          await client.request("chat.submit", {
            message: trimmed,
            trace: options.traceMode !== "off",
          }),
        );

        const responseText =
          typeof response.assistant_response === "string"
            ? response.assistant_response.trim()
            : "";
        const pendingText = pendingFinalTextRef.current.trim();
        const finalText =
          pendingText || responseText || streamingRef.current.trim();
        if (finalText) {
          appendLine("assistant", finalText);
        }
        setStreamingText("");
        setWorkingPhase("idle");
      } catch (error) {
        setWorkingPhase("idle");
        appendLine("error", String(error));
      } finally {
        pendingFinalTextRef.current = "";
        turnInFlightRef.current = false;
      }
    },
    [appendLine, client, options.traceMode, runSlashCommand],
  );

  useEffect(() => {
    let alive = true;
    client.start();

    const disposeEvents = client.onEvent((event: BridgeEvent) => {
      if (!alive) {
        return;
      }
      if (event.event !== "chat.event") {
        return;
      }

      const kind = String(event.params.kind ?? "");
      const text = String(event.params.text ?? "");
      const payload = event.params.payload;
      appendEvent(kind, text, payload);

      if (kind === "assistant_token") {
        setStreamingText((previous) => previous + text);
        return;
      }
      if (kind === "assistant_token_batch") {
        // Batched tokens — text contains all accumulated tokens
        setStreamingText((previous) => previous + text);
        return;
      }
      if (kind === "tool_call") {
        setWorkingPhase("tool");
        const toolText = text.trim() || formatValue(payload);
        appendLine("tool", toolText === "{}" ? "tool call started" : toolText);
        return;
      }
      if (kind === "error") {
        setWorkingPhase("idle");
        appendLine("error", text || formatValue(payload));
        setStreamingText("");
        return;
      }
      if (kind === "final" || kind === "cancelled") {
        const finalText = text.trim() || streamingRef.current.trim();

        // Hold final text until request resolves so assistant line is always last.
        pendingFinalTextRef.current = finalText;

        if (finalText && !turnInFlightRef.current) {
          appendLine("assistant", finalText);
        }
        setStreamingText("");
        setWorkingPhase("idle");
      }
    });

    const disposeErrorListener = client.onError((error) => {
      appendLine("error", `Bridge error: ${error.message}`);
      setStatus("Bridge disconnected (will auto-reconnect)");
    });

    void (async () => {
      try {
        const response = parseBridgeSessionInit(
          await client.request("session.init", {}),
        );
        if (!alive) {
          return;
        }
        const loadedSessionId = String(response.session_id ?? "");
        setSessionId(loadedSessionId);
        const toolCommands = Array.isArray(response.commands?.tool_commands)
          ? response.commands?.tool_commands
          : [];
        const wrapperCommands = Array.isArray(
          response.commands?.wrapper_commands,
        )
          ? response.commands?.wrapper_commands
          : [];
        const merged = Array.from(
          new Set([...DEFAULT_COMMANDS, ...wrapperCommands, ...toolCommands]),
        );
        setCommands(merged);
        setStatus(`Connected (${loadedSessionId || "session"})`);
        await fetchSettings();
      } catch (error) {
        setStatus(`Bridge connection failed: ${String(error)}`);
      }
    })();

    return () => {
      alive = false;
      disposeEvents();
      disposeErrorListener();
      void client.shutdown();
    };
  }, [appendEvent, appendLine, client, fetchSettings]);

  useEffect(() => {
    const debounce = mentionDebounceRef.current;
    if (overlayView !== "none") {
      debounce.clear();
      setMentionItems([]);
      return;
    }
    if (mentionQuery === null) {
      debounce.clear();
      setMentionItems([]);
      setMentionIndex(0);
      return;
    }

    debounce.schedule((requestToken) => {
      void (async () => {
        try {
          const response = parseBridgeMentionSearch(
            await client.request("mentions.search", {
              query: mentionQuery,
              limit: 16,
            }),
          );
          if (!debounce.isCurrent(requestToken)) {
            return;
          }
          setMentionItems(response.items);
          setMentionIndex(0);
        } catch {
          if (debounce.isCurrent(requestToken)) {
            setMentionItems([]);
            setMentionIndex(0);
          }
        }
      })();
    });

    return () => {
      debounce.clear();
    };
  }, [client, mentionQuery, overlayView]);

  useEffect(() => {
    const commandTrigger =
      overlayView === "none" &&
      composerValue.startsWith("/") &&
      !composerValue.slice(1).includes(" ");
    if (commandTrigger) {
      goToRootPalette();
      return;
    }
    const shouldCloseRootPalette =
      overlayView === "palette-root" &&
      (!composerValue.startsWith("/") || composerValue.slice(1).includes(" "));
    if (shouldCloseRootPalette) {
      setOverlayStack([]);
    }
  }, [composerValue, goToRootPalette, overlayView]);

  useEffect(() => {
    const maxItems =
      overlayView === "palette-root"
        ? rootItems.length
        : overlayView === "palette-settings"
          ? settingsItems.length
          : 0;
    setPaletteIndex((previous) => clampIndex(previous, maxItems));
  }, [overlayView, rootItems.length, settingsItems.length]);

  useInput((input, key) => {
    if (key.ctrl && input.toLowerCase() === "c") {
      void client.shutdown().finally(() => exit());
      return;
    }

    if (key.ctrl && input.toLowerCase() === "l") {
      setTranscriptLines([]);
      setStreamingText("");
      setComposerValue("");
      clearInlineEventFeed();
      return;
    }

    if (key.ctrl && input.toLowerCase() === "d") {
      if (workingPhase !== "idle") {
        void client.request("chat.cancel", {}).catch(() => {
          // Ignore cancel errors
        });
        setWorkingPhase("idle");
        setStreamingText("");
        appendLine("status", "Chat cancelled");
      }
      return;
    }

    if (key.escape) {
      if (
        overlayView === "settings-edit" ||
        overlayView === "palette-settings"
      ) {
        closeOneOverlay();
        return;
      }
      if (overlayView === "status-panel") {
        closeOneOverlay();
        return;
      }
      if (overlayView === "palette-root") {
        setOverlayStack([]);
        if (composerValue.startsWith("/")) {
          setComposerValue("");
        }
        return;
      }
      if (mentionSuggestions.length > 0) {
        setMentionItems([]);
        setMentionIndex(0);
        return;
      }
      setComposerValue("");
      return;
    }

    if (overlayView === "palette-root" || overlayView === "palette-settings") {
      const items = overlayView === "palette-root" ? rootItems : settingsItems;
      if (key.upArrow || input === "k") {
        setPaletteIndex((previous) => moveIndex(previous, -1, items.length));
        return;
      }
      if (key.downArrow || input === "j") {
        setPaletteIndex((previous) => moveIndex(previous, 1, items.length));
        return;
      }
      if (key.tab || key.return) {
        void applyActiveSelection();
      }
      return;
    }

    if (mentionSuggestions.length > 0) {
      if (key.upArrow) {
        setMentionIndex((previous) =>
          moveIndex(previous, -1, mentionSuggestions.length),
        );
        return;
      }
      if (key.downArrow) {
        setMentionIndex((previous) =>
          moveIndex(previous, 1, mentionSuggestions.length),
        );
        return;
      }
      if (key.tab) {
        void applyActiveSelection();
      }
    }
  });

  const submitSettingsEdit = useCallback(
    async (value: string): Promise<void> => {
      const nextValue = value.trim();
      await client.request("settings.update", {
        updates: { [settingsEditKey]: nextValue },
      });
      appendLine("status", `Updated ${settingsEditKey}`);
      await fetchSettings();
      closeOneOverlay();
    },
    [appendLine, client, closeOneOverlay, fetchSettings, settingsEditKey],
  );

  const renderPalette = (): React.JSX.Element | null => {
    if (overlayView === "palette-root") {
      return (
        <PalettePanel
          title="Command palette"
          breadcrumb="Root"
          items={rootItems}
          selectedIndex={paletteIndex}
          emptyLabel="No commands match your query."
        />
      );
    }
    if (overlayView === "palette-settings") {
      return (
        <PalettePanel
          title="Settings"
          breadcrumb="Root / Settings"
          items={settingsItems}
          selectedIndex={paletteIndex}
          emptyLabel="No settings options available."
        />
      );
    }
    return null;
  };

  return (
    <ShellFrame>
      <HeaderBar
        status={status}
        sessionId={sessionId}
        workingPhase={workingPhase}
      />

      <Box marginTop={1} flexDirection="column">
        <TranscriptStatic lines={transcriptLines} />
        <TranscriptLive streamingText={streamingText} />
      </Box>

      {overlayView === "settings-edit" ? (
        <SettingsEditor
          keyName={settingsEditKey}
          value={settingsEditValue}
          onChange={setSettingsEditValue}
          onSubmit={(value) => {
            void submitSettingsEdit(value);
          }}
        />
      ) : null}

      {overlayView === "status-panel" ? (
        <StatusPanel payload={statusPayload} />
      ) : null}

      {overlayView === "none" && mentionSuggestions.length > 0 ? (
        <MentionPanel items={mentionSuggestions} selectedIndex={mentionIndex} />
      ) : null}

      <Composer
        value={composerValue}
        placeholder={PLACEHOLDER}
        disabled={overlayView === "settings-edit"}
        onChange={setComposerValue}
        onSubmit={(line) => {
          if (overlayView === "settings-edit") {
            return;
          }
          if (
            overlayView === "palette-root" ||
            overlayView === "palette-settings"
          ) {
            void applyActiveSelection();
            return;
          }
          if (mentionSuggestions.length > 0) {
            void applyActiveSelection();
            return;
          }
          void submitComposerLine(line);
        }}
      />

      {renderPalette()}

      {overlayView === "palette-settings" ? (
        <Text color="gray">
          Model: {settingsSnapshot.masked_values.DSPY_LM_MODEL || "<unset>"} •
          API base:{" "}
          {settingsSnapshot.masked_values.DSPY_LM_API_BASE || "<unset>"}
        </Text>
      ) : null}

      <StatusLine overlayView={overlayView} workingPhase={workingPhase} />
    </ShellFrame>
  );
}

const options = parseCliOptions(process.argv.slice(2));
suppressKnownInkWarnings();
render(<FleetInkApp options={options} />);

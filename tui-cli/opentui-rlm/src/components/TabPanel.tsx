/**
 * TabPanel - tabbed view for Reasoning, Tools, and Stats panes.
 * Polished with surface background, styled tabs, and formatted content.
 */

import { useAppContext } from "../context/AppContext";
import { useRegisterKeyHandler, PRIORITY } from "../context/KeyboardContext";
import { bg, border, fg, accent, semantic } from "../theme";
import { useRef, useEffect, useCallback } from "react";

function ReasoningPane() {
  const { state } = useAppContext();
  const lines = state.currentTurn.reasoningLines;
  const scrollRef = useRef<any>(null);

  useEffect(() => {
    if (scrollRef.current?.scrollToBottom) {
      scrollRef.current.scrollToBottom();
    }
  }, [lines.length]);

  if (lines.length === 0) {
    return (
      <box padding={2}>
        <text fg={fg.muted}>No reasoning steps yet.</text>
      </box>
    );
  }

  return (
    <scrollbox
      ref={scrollRef}
      flexGrow={1}
      padding={1}
      stickyScroll
      stickyStart="bottom"
      style={{ scrollbarOptions: { showArrows: true, trackOptions: { foregroundColor: accent.base, backgroundColor: bg.highlight } } }}
    >
      {lines.map((line, i) => (
        <box key={i} paddingTop={i > 0 ? 1 : 0} paddingLeft={1} paddingRight={1}>
          <text fg={fg.secondary}>
            <span fg={fg.muted}>{String(i + 1).padStart(2, " ")} │ </span>
            <span fg={accent.base}>▸ </span>
            {line}
          </text>
        </box>
      ))}
    </scrollbox>
  );
}

function ToolsPane() {
  const { state } = useAppContext();
  const timeline = state.currentTurn.toolTimeline;
  const scrollRef = useRef<any>(null);

  useEffect(() => {
    if (scrollRef.current?.scrollToBottom) {
      scrollRef.current.scrollToBottom();
    }
  }, [timeline.length]);

  const getToolIcon = (entry: string): { icon: string; color: string } => {
    const lower = entry.toLowerCase();
    if (lower.includes("error") || lower.includes("failed")) {
      return { icon: "✗", color: semantic.error };
    }
    if (lower.includes("result") || lower.includes("completed")) {
      return { icon: "✓", color: semantic.success };
    }
    if (lower.includes("calling") || lower.includes("executing")) {
      return { icon: "◆", color: accent.base };
    }
    return { icon: "◆", color: semantic.success };
  };

  if (timeline.length === 0) {
    return (
      <box padding={2}>
        <text fg={fg.muted}>No tool calls yet.</text>
      </box>
    );
  }

  return (
    <scrollbox
      ref={scrollRef}
      flexGrow={1}
      padding={1}
      stickyScroll
      stickyStart="bottom"
      style={{ scrollbarOptions: { showArrows: true, trackOptions: { foregroundColor: accent.base, backgroundColor: bg.highlight } } }}
    >
      {timeline.map((entry, i) => {
        const { icon, color } = getToolIcon(entry);
        const showSeparator = i > 0 && i % 5 === 0;

        return (
          <>
            {showSeparator && (
              <box paddingTop={1}>
                <text fg={fg.muted}>─{"".repeat(25)}</text>
              </box>
            )}
            <box key={i} paddingTop={i > 0 ? 1 : 0} paddingLeft={1} paddingRight={1}>
              <text fg={fg.secondary}>
                <span fg={fg.muted}>{String(i + 1).padStart(2, " ")} │ </span>
                <span fg={color}>{icon} </span>
                {entry}
              </text>
            </box>
          </>
        );
      })}
    </scrollbox>
  );
}

function StatsPane() {
  const { state } = useAppContext();
  const turn = state.currentTurn;

  // Calculate derived metrics
  const avgTokensPerTurn = turn.historyTurns > 0
    ? Math.round(turn.tokenCount / turn.historyTurns)
    : turn.tokenCount;

  const errorCount = state.transcript.filter(
    (e) => e.role === "system" && e.content.startsWith("Error:")
  ).length;

  return (
    <box flexDirection="column" gap={1} padding={2}>
      <text>
        <span fg={fg.secondary}>Tokens: </span>
        <span fg={fg.primary}>{turn.tokenCount}</span>
      </text>
      <text>
        <span fg={fg.secondary}>Avg/turn: </span>
        <span fg={fg.primary}>{avgTokensPerTurn}</span>
      </text>
      <text>
        <span fg={fg.secondary}>History turns: </span>
        <span fg={fg.primary}>{turn.historyTurns}</span>
      </text>
      <text>
        <span fg={fg.secondary}>Reasoning steps: </span>
        <span fg={fg.primary}>{turn.reasoningLines.length}</span>
      </text>
      <text>
        <span fg={fg.secondary}>Tool calls: </span>
        <span fg={fg.primary}>{turn.toolTimeline.length}</span>
      </text>
      {errorCount > 0 && (
        <text>
          <span fg={fg.secondary}>Errors: </span>
          <span fg={semantic.error}>{errorCount}</span>
        </text>
      )}
      <text fg={fg.muted}>{"─".repeat(15)}</text>
      <text>
        <span fg={fg.secondary}>Status: </span>
        <span fg={fg.primary}>{state.statusMessage}</span>
      </text>
      <text>
        <span fg={fg.secondary}>Connection: </span>
        <span fg={state.connectionState === "connected" ? semantic.success : fg.secondary}>
          {state.connectionState}
        </span>
      </text>
    </box>
  );
}

const TABS = ["reasoning", "tools", "stats"] as const;
const TAB_NAMES = ["Reasoning", "Tools", "Stats"];

interface TabPanelProps {
  focused?: boolean;
  onFocus?: () => void;
}

export function TabPanel({ focused = false, onFocus }: TabPanelProps) {
  const { state, dispatch } = useAppContext();
  const activeTab = state.activeTab;
  const activeIndex = TABS.indexOf(activeTab);

  const handleTabKeys = useCallback((key: { ctrl: boolean; shift: boolean; name: string }) => {
    if (state.sidebarCollapsed) return false;

    if (key.ctrl && key.name === "1") {
      dispatch({ type: "SET_ACTIVE_TAB", payload: "reasoning" });
      return true;
    }
    if (key.ctrl && key.name === "2") {
      dispatch({ type: "SET_ACTIVE_TAB", payload: "tools" });
      return true;
    }
    if (key.ctrl && key.name === "3") {
      dispatch({ type: "SET_ACTIVE_TAB", payload: "stats" });
      return true;
    }
    return false;
  }, [state.sidebarCollapsed, dispatch]);

  useRegisterKeyHandler("tabPanel", handleTabKeys, PRIORITY.PANE);

  return (
    <box
      flexGrow={1}
      flexDirection="column"
      backgroundColor={bg.surface}
      border
      borderStyle="rounded"
      borderColor={focused ? accent.base : border.dim}
      title=" Inspector "
      titleAlignment="center"
      onMouseDown={onFocus}
    >
      {/* Tab headers */}
      <box
        height={1}
        flexDirection="row"
        paddingLeft={1}
        paddingRight={1}
        paddingTop={1}
        backgroundColor={bg.highlight}
      >
        <tab-select
          options={[
            { name: "Reasoning", description: "Agent thinking process (Ctrl+1)" },
            { name: "Tools", description: "Tool calls and results (Ctrl+2)" },
            { name: "Stats", description: "Session statistics (Ctrl+3)" },
          ]}
          onChange={(index) => {
            dispatch({ type: "SET_ACTIVE_TAB", payload: TABS[index]! });
          }}
          focused
        />
      </box>

      {/* Tab content */}
      <box flexGrow={1}>
        {activeTab === "reasoning" && <ReasoningPane />}
        {activeTab === "tools" && <ToolsPane />}
        {activeTab === "stats" && <StatsPane />}
      </box>
    </box>
  );
}

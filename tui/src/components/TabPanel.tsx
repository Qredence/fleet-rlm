/**
 * TabPanel - tabbed view for Reasoning, Tools, and Stats panes.
 * Polished with surface background, styled tabs, and formatted content.
 */

import { useAppContext } from "../context/AppContext";
import { bg, border, fg, accent, semantic } from "../theme";
import { useKeyboard } from "@opentui/react";

function ReasoningPane() {
  const { state } = useAppContext();
  const lines = state.currentTurn.reasoningLines;

  if (lines.length === 0) {
    return (
      <box padding={2}>
        <text fg={fg.muted}>No reasoning steps yet.</text>
      </box>
    );
  }

  return (
    <scrollbox flexGrow={1} padding={1}>
      {lines.map((line, i) => (
        <box key={i} paddingLeft={1} paddingRight={1} paddingTop={0} paddingBottom={0}>
          <text fg={fg.secondary}>
            <span fg={fg.muted}>{" ▸ "}</span>
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

  if (timeline.length === 0) {
    return (
      <box padding={2}>
        <text fg={fg.muted}>No tool calls yet.</text>
      </box>
    );
  }

  return (
    <scrollbox flexGrow={1} padding={1}>
      {timeline.map((entry, i) => (
        <box key={i} paddingLeft={1} paddingRight={1} paddingTop={0} paddingBottom={0}>
          <text fg={fg.secondary}>
            <span fg={semantic.success}>{" ◆ "}</span>
            {entry}
          </text>
        </box>
      ))}
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

export function TabPanel() {
  const { state, dispatch } = useAppContext();
  const activeTab = state.activeTab;
  const activeIndex = TABS.indexOf(activeTab);

  useKeyboard((key) => {
    if (key.ctrl && key.name === "1") {
      dispatch({ type: "SET_ACTIVE_TAB", payload: "reasoning" });
    }
    if (key.ctrl && key.name === "2") {
      dispatch({ type: "SET_ACTIVE_TAB", payload: "tools" });
    }
    if (key.ctrl && key.name === "3") {
      dispatch({ type: "SET_ACTIVE_TAB", payload: "stats" });
    }
    if (key.name === "tab" || key.name === "right") {
      const nextIndex = (activeIndex + 1) % TABS.length;
      dispatch({ type: "SET_ACTIVE_TAB", payload: TABS[nextIndex]! });
    }
    if ((key.name === "tab" && key.shift) || key.name === "left") {
      const prevIndex = (activeIndex - 1 + TABS.length) % TABS.length;
      dispatch({ type: "SET_ACTIVE_TAB", payload: TABS[prevIndex]! });
    }
  });

  return (
    <box
      flexGrow={1}
      flexDirection="column"
      backgroundColor={bg.surface}
      border
      borderStyle="rounded"
      borderColor={border.dim}
      title=" Inspector "
      titleAlignment="center"
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

/**
 * StatusBar component - displays connection state and session info.
 * Polished with surface background, styled indicators, and clean spacing.
 */

import { useAppContext } from "../context/AppContext";
import { bg, border, fg, accent, semantic } from "../theme";

export function StatusBar() {
  const { state } = useAppContext();

  const connectionConfig = {
    connected: { color: semantic.success, label: "Connected" },
    connecting: { color: semantic.warning, label: "Connecting..." },
    error: { color: semantic.error, label: "Error" },
    disconnected: { color: fg.muted, label: "Disconnected" },
  } as const;

  const conn = connectionConfig[state.connectionState];

  return (
    <box
      backgroundColor={bg.elevated}
      width="100%"
      paddingTop={1}
      paddingBottom={1}
      paddingLeft={2}
      paddingRight={2}
      flexDirection="row"
      gap={2}
    >
      <text>
        <span fg={conn.color}>●</span>
        <span fg={fg.secondary}> {conn.label}</span>
      </text>

      <text fg={fg.muted}>│</text>

      <text>
        <span fg={accent.base}>fleet-rlm</span>
      </text>

      <text fg={fg.muted}>│</text>

      <text fg={fg.secondary}>{state.statusMessage}</text>

      <>
        <text fg={fg.muted}>│</text>
        <text>
          <span fg={fg.secondary}>Workspace: </span>
          <span fg={fg.primary}>{state.workspaceId}</span>
        </text>
      </>

      <>
        <text fg={fg.muted}>│</text>
        <text>
          <span fg={fg.secondary}>User: </span>
          <span fg={fg.primary}>{state.userId}</span>
        </text>
      </>

      {state.sessionId && (
        <>
          <text fg={fg.muted}>│</text>
          <text>
            <span fg={fg.secondary}>Session: </span>
            <span fg={fg.primary}>{state.sessionId.slice(0, 8)}</span>
          </text>
        </>
      )}

      {state.docsPath && (
        <>
          <text fg={fg.muted}>│</text>
          <text>
            <span fg={fg.secondary}>📄 </span>
            <span fg={fg.primary}>{state.docsPath}</span>
          </text>
        </>
      )}

      {state.currentTurn.historyTurns > 0 && (
        <>
          <text fg={fg.muted}>│</text>
          <text>
            <span fg={fg.secondary}>Turns: </span>
            <span fg={fg.primary}>{state.currentTurn.historyTurns}</span>
          </text>
        </>
      )}

      {state.currentTurn.tokenCount > 0 && (
        <>
          <text fg={fg.muted}>│</text>
          <text>
            <span fg={fg.secondary}>Tokens: </span>
            <span fg={fg.primary}>{state.currentTurn.tokenCount}</span>
          </text>
        </>
      )}
    </box>
  );
}

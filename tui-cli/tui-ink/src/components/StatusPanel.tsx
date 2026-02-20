import { Box, Text } from "ink";
import type React from "react";

interface StatusPanelProps {
  payload: Record<string, unknown> | null;
}

function stringifyValue(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (value === null || value === undefined) {
    return "-";
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function getStatusIcon(key: string): string {
  const iconMap: Record<string, string> = {
    planner_model: "🤖",
    planner_configured: "✓",
    modal_authenticated: "🔐",
    modal_volume: "💾",
    documents_loaded: "📄",
    secret_configured: "🔑",
    permissions: "🛡️",
  };
  return iconMap[key] || "▸";
}

export function StatusPanel({ payload }: StatusPanelProps): React.JSX.Element {
  const rows = payload
    ? Object.entries(payload).slice(0, 18)
    : ([["status", "No status loaded"]] as Array<[string, unknown]>);
  return (
    <Box
      marginTop={1}
      borderStyle="round"
      borderColor="green"
      paddingX={1}
      paddingY={1}
      flexDirection="column"
    >
      <Text color="green" bold>
        📊 Status panel
      </Text>
      <Text color="gray" dimColor>
        Esc to close
      </Text>
      <Box marginY={1}>
        <Text color="gray">────────────────────────────────────────</Text>
      </Box>
      {rows.map(([key, value]) => (
        <Box key={key} marginBottom={0}>
          <Box width={28}>
            <Text color="gray">
              {getStatusIcon(key)} {key}
            </Text>
          </Box>
          <Text>{stringifyValue(value)}</Text>
        </Box>
      ))}
    </Box>
  );
}

import { Box, Text } from "ink";
import { ScrollList } from "ink-scroll-list";
import type React from "react";

import type { MentionItem } from "../types.js";

interface MentionPanelProps {
  items: MentionItem[];
  selectedIndex: number;
}

function getFileIcon(path: string): string {
  if (path.endsWith("/")) return "📁";
  const ext = path.split(".").pop()?.toLowerCase() || "";
  const iconMap: Record<string, string> = {
    ts: "🔷",
    tsx: "⚛️",
    js: "📜",
    jsx: "⚛️",
    py: "🐍",
    json: "📋",
    yaml: "⚙️",
    yml: "⚙️",
    md: "📝",
    txt: "📄",
  };
  return iconMap[ext] || "📄";
}

export function MentionPanel({ items, selectedIndex }: MentionPanelProps): React.JSX.Element {
  if (items.length === 0) {
    return (
      <Box marginTop={1}>
        <Text color="gray">No file matches.</Text>
      </Box>
    );
  }

  return (
    <Box marginTop={1} borderStyle="round" borderColor="cyan" paddingX={1} flexDirection="column">
      <Text color="cyan" bold>
        📎 File mentions
      </Text>
      <Box height={Math.min(10, Math.max(4, items.length + 1))} marginTop={1}>
        <ScrollList selectedIndex={selectedIndex} scrollAlignment="auto">
          {items.map((item, index) => (
            <Text key={item.path} color={index === selectedIndex ? "cyan" : "white"}>
              {index === selectedIndex ? "▸ " : "  "}
              {getFileIcon(item.path)} {item.path}
            </Text>
          ))}
        </ScrollList>
      </Box>
      <Box marginTop={1}>
        <Text color="gray">↑/↓ navigate • Enter select • Esc cancel</Text>
      </Box>
    </Box>
  );
}

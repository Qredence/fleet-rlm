import { Box, Text } from "ink";
import type React from "react";

interface PermissionCardProps {
  command: string;
  description?: string;
  selectedOption: number;
  onSelect: (option: "allow_once" | "allow_session" | "deny") => void;
}

const PERMISSION_OPTIONS = [
  { id: "allow_once", label: "✓ Allow once", description: "Execute this command now" },
  {
    id: "allow_session",
    label: "✓✓ Allow for session",
    description: "Don't ask again this session",
  },
  { id: "deny", label: "✗ Deny", description: "Cancel this action" },
] as const;

export function PermissionCard({
  command,
  description,
  selectedOption,
}: PermissionCardProps): React.JSX.Element {
  return (
    <Box
      marginTop={1}
      marginBottom={1}
      borderStyle="round"
      borderColor="yellow"
      paddingX={2}
      paddingY={1}
      flexDirection="column"
    >
      <Text color="yellow" bold>
        ⚠️ Permission required
      </Text>
      <Box marginTop={1} marginBottom={1}>
        <Text>
          Command{" "}
          <Text color="cyan" bold>
            {command}
          </Text>{" "}
          requires authorization
        </Text>
      </Box>
      {description && (
        <Box marginBottom={1}>
          <Text color="gray" dimColor>
            {description}
          </Text>
        </Box>
      )}
      <Box marginY={1}>
        <Text color="gray">────────────────────────────────────────</Text>
      </Box>
      {PERMISSION_OPTIONS.map((option, index) => (
        <Box key={option.id} marginY={0} flexDirection="column">
          <Text
            color={index === selectedOption ? (option.id === "deny" ? "red" : "cyan") : "white"}
            bold={index === selectedOption}
          >
            {index === selectedOption ? "▸ " : "  "}
            {option.label}
          </Text>
          <Text color="gray" dimColor>
            {"  "} {option.description}
          </Text>
        </Box>
      ))}
      <Box marginTop={1}>
        <Text color="gray" dimColor>
          ↑/↓ select • Enter confirm • Esc cancel
        </Text>
      </Box>
    </Box>
  );
}

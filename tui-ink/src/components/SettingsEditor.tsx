import { Box, Text } from "ink";
import TextInput from "ink-text-input";
import type React from "react";

interface SettingsEditorProps {
  keyName: "DSPY_LM_MODEL" | "DSPY_LM_API_BASE";
  value: string;
  onChange: (next: string) => void;
  onSubmit: (value: string) => void;
}

function getKeyIcon(keyName: string): string {
  if (keyName.includes("MODEL")) return "🤖";
  if (keyName.includes("API_BASE")) return "🔌";
  return "⚙️";
}

export function SettingsEditor({
  keyName,
  value,
  onChange,
  onSubmit,
}: SettingsEditorProps): React.JSX.Element {
  return (
    <Box
      marginTop={1}
      borderStyle="round"
      borderColor="yellow"
      paddingX={2}
      paddingY={1}
      flexDirection="column"
    >
      <Text color="yellow" bold>
        ⚙️ Settings / Model & Provider
      </Text>
      <Text color="gray" dimColor>
        Editing configuration
      </Text>
      <Box marginY={1}>
        <Text color="gray">────────────────────────────────────────</Text>
      </Box>
      <Box marginBottom={1}>
        <Text color="gray">{getKeyIcon(keyName)}</Text>
        <Text> </Text>
        <Text color="cyan">{keyName}</Text>
      </Box>
      <Box marginBottom={1} paddingLeft={2}>
        <TextInput
          value={value}
          onChange={onChange}
          onSubmit={onSubmit}
          placeholder="Enter value..."
        />
      </Box>
      <Box marginTop={1}>
        <Text color="gray" dimColor>
          Enter save • Esc cancel
        </Text>
      </Box>
    </Box>
  );
}

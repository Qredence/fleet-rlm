import { Box } from "ink";
import TextInput from "ink-text-input";
import type React from "react";

import { Text } from "./Text.js";
import { inkColors } from "./colors.js";

interface ComposerProps {
  value: string;
  placeholder: string;
  disabled?: boolean;
  onChange: (next: string) => void;
  onSubmit: (submitted: string) => void;
}

/**
 * Minimalistic composer/input box inspired by Letta Code.
 * Clean border, no emojis, simple prompt indicator.
 */
export function Composer({
  value,
  placeholder,
  disabled = false,
  onChange,
  onSubmit,
}: ComposerProps): React.JSX.Element {
  const borderColor = disabled ? inkColors.dim : inkColors.accentBright;
  const promptChar = "›";

  return (
    <Box marginTop={1} borderStyle="round" borderColor={borderColor} paddingX={2} paddingY={0}>
      <Text color={borderColor}>{promptChar}</Text>
      <Text> </Text>
      {disabled ? (
        <Text color={inkColors.dim}>{value || placeholder}</Text>
      ) : (
        <TextInput
          value={value}
          onChange={onChange}
          onSubmit={onSubmit}
          placeholder={placeholder}
        />
      )}
    </Box>
  );
}

/**
 * HintsBar component - displays keyboard shortcuts at the bottom.
 * Polished with elevated background and styled key badges.
 */

import { bg, fg, accent } from "../theme";

function KeyHint({ keyLabel, desc }: { keyLabel: string; desc: string }) {
  return (
    <text>
      <span fg={accent.base}>{keyLabel}</span>
      <span fg={fg.secondary}> {desc}</span>
    </text>
  );
}

export function HintsBar() {
  return (
    <box
      height={1}
      width="100%"
      backgroundColor={bg.elevated}
      paddingLeft={2}
      paddingRight={2}
      paddingTop={1}
      paddingBottom={1}
      flexDirection="row"
      gap={4}
    >
      <KeyHint keyLabel="Ctrl+C" desc="Cancel" />
      <KeyHint keyLabel="Ctrl+L" desc="Clear" />
      <KeyHint keyLabel="Ctrl+Y" desc="Copy" />
      <KeyHint keyLabel="F2" desc="Reasoning" />
      <KeyHint keyLabel="F3" desc="Tools" />
      <KeyHint keyLabel="/help" desc="Commands" />
    </box>
  );
}

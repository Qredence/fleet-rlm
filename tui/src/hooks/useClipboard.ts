/**
 * Clipboard utility using OSC 52 escape sequence.
 * Works in terminals that support ANSI clipboard operations (iTerm2, Alacritty, Kitty, WezTerm, etc.)
 */

export function copyToClipboard(text: string): boolean {
  try {
    // Encode text as base64
    const encoded = Buffer.from(text).toString("base64");

    // OSC 52 escape sequence to copy to clipboard
    // \x1b]52;c;encoded_text\x07
    const oscSequence = `\x1b]52;c;${encoded}\x07`;

    // Write to stdout (works in terminal environments)
    if (typeof process !== "undefined" && process.stdout) {
      process.stdout.write(oscSequence);
      return true;
    }

    return false;
  } catch (error) {
    console.error("Failed to copy to clipboard:", error);
    return false;
  }
}

export function getLastAssistantMessage(messages: { role: string; content: string }[]): string | null {
  // Find the last assistant message
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i]?.role === "assistant") {
      return messages[i]?.content || null;
    }
  }
  return null;
}

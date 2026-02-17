/**
 * Fleet UI Color System
 *
 * Inspired by Letta Code, minimalist and clean.
 * No emojis, using simple Unicode indicators (●, ⎿, etc.)
 */

export const colors = {
  // Primary accent (light blue)
  accent: "#8C8CF9" as const,
  accentLight: "#BEBEEE" as const,

  // Text colors
  text: {
    main: "#DEE1E4" as const,
    secondary: "#A5A8AB" as const,
    disabled: "#46484A" as const,
  },

  // Status colors
  status: {
    success: "#64CF64" as const,
    warning: "#FEE19C" as const,
    error: "#F1689F" as const,
    info: "#8C8CF9" as const,
  },

  // Command palette
  palette: {
    border: "#46484A" as const,
    title: "#8C8CF9" as const,
    itemDefault: "#A5A8AB" as const,
    itemSelected: "#8C8CF9" as const,
    description: "#46484A" as const,
  },

  // Tool indicators
  tool: {
    running: "#FEE19C" as const, // yellow/dim
    completed: "#64CF64" as const, // green
    error: "#F1689F" as const, // red
    pending: "#A5A8AB" as const, // gray
  },

  // Input/composer
  input: {
    border: "#46484A" as const,
    text: "#DEE1E4" as const,
  },

  // Events/reasoning
  event: {
    thinking: "#A5A8AB" as const, // dim gray
    toolCall: "#8C8CF9" as const, // accent
    toolResult: "#A5A8AB" as const, // dim gray
    reasoning: "#A5A8AB" as const, // dim gray
  },
} as const;

// Ink color mappings (subset of Ink's supported colors)
export const inkColors = {
  accentBright: "cyan" as const,
  accent: "blue" as const,
  success: "green" as const,
  warning: "yellow" as const,
  error: "red" as const,
  info: "cyan" as const,
  dim: "gray" as const,
  default: "white" as const,
} as const;

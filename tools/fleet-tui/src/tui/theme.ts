/** Fleet's achromatic terminal palette and visual vocabulary. */

export const theme = {
  ink: "#f4f4f4",
  muted: "#8a8a8a",
  faint: "#5f5f5f",
  paper: "#ffffff",
  background: "#111111",
  rule: "#555555",
} as const;

let terminalColorScheme: "dark" | "light" = "dark";

export function setTerminalColorScheme(scheme: "dark" | "light"): void {
  terminalColorScheme = scheme;
}

export const ansi = {
  reset: "\x1b[0m",
  bold: "\x1b[1m",
  boldOff: "\x1b[22m",
  dim: "\x1b[2m",
  dimOff: "\x1b[22m",
  italic: "\x1b[3m",
  italicOff: "\x1b[23m",
  get white() {
    return terminalColorScheme === "light" ? "\x1b[30m" : "\x1b[97m";
  },
  get gray() {
    return terminalColorScheme === "light" ? "\x1b[90m" : "\x1b[90m";
  },
  black: "\x1b[30m",
  bgWhite: "\x1b[47m",
} as const;

export const statusGlyph = {
  success: "✓",
  warning: "!",
  error: "×",
  running: "…",
  idle: "·",
} as const;

export const markdownTheme = {
  heading: (text: string) => `${ansi.bold}${ansi.white}${text}${ansi.reset}`,
  link: (text: string) => `${ansi.white}${text}${ansi.reset}`,
  linkUrl: (text: string) => `${ansi.gray}${text}${ansi.reset}`,
  code: (text: string) => `${ansi.bold}${text}${ansi.boldOff}`,
  codeBlock: (text: string) => text,
  codeBlockBorder: (text: string) => `${ansi.gray}${text}${ansi.reset}`,
  quote: (text: string) => `${ansi.dim}${text}${ansi.dimOff}`,
  quoteBorder: (text: string) => `${ansi.gray}${text}${ansi.reset}`,
  hr: (text: string) => `${ansi.gray}${text}${ansi.reset}`,
  listBullet: (text: string) => `${ansi.white}${text}${ansi.reset}`,
  bold: (text: string) => `${ansi.bold}${text}${ansi.boldOff}`,
  italic: (text: string) => `${ansi.italic}${text}${ansi.italicOff}`,
  strikethrough: (text: string) => text,
  underline: (text: string) => `\x1b[4m${text}\x1b[24m`,
};

export const selectTheme = {
  selectedPrefix: (text: string) => `${ansi.white}${text}${ansi.reset}`,
  selectedText: (text: string) => `${ansi.bold}${ansi.white}${text}${ansi.reset}`,
  description: (text: string) => `${ansi.dim}${text}${ansi.dimOff}`,
  scrollInfo: (text: string) => `${ansi.dim}${text}${ansi.dimOff}`,
  noMatch: (text: string) => `${ansi.dim}${text}${ansi.dimOff}`,
};

export const editorTheme = {
  borderColor: (text: string) => `${ansi.gray}${text}${ansi.reset}`,
  selectList: selectTheme,
};

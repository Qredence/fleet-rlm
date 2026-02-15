/**
 * Spinner component for OpenTUI with animation frames
 * Based on opentui-spinner reference implementation
 */

import { useEffect, useState } from "react";
import { z } from "zod";

// Spinner frame definitions (from cli-spinners)
export const spinnerFrames = {
  dots: ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"],
  dots2: ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"],
  line: ["-", "\\", "|", "/"],
  pipe: ["┤", "┘", "┴", "└", "├", "┌", "┬", "┐"],
  star: ["✶", "✸", "✹", "✺", "✹", "✷"],
  arc: ["◜", "◠", "◝", "◞", "◡", "◟"],
  circle: ["◐", "◓", "◑", "◒"],
  squareCorners: ["◰", "◳", "◲", "◱"],
  circleQuarters: ["◴", "◷", "◶", "◵"],
  circleHalves: ["◐", "◓", "◑", "◒"],
  bouncingBar: [
    "[    ]",
    "[   =]",
    "[  ==]",
    "[ ===]",
    "[====]",
    "[=== ]",
    "[==  ]",
    "[=   ]",
  ],
  bouncingBall: [
    "( ●    )",
    "(  ●   )",
    "(   ●  )",
    "(    ● )",
    "(     ●)",
    "(    ● )",
    "(   ●  )",
    "(  ●   )",
    "( ●    )",
    "(●     )",
  ],
  arrow: ["←", "↖", "↑", "↗", "→", "↘", "↓", "↙"],
  hamburger: ["☱", "☲", "☴"],
  growVertical: ["▁", "▃", "▄", "▅", "▆", "▇", "▆", "▅", "▄", "▃"],
  growHorizontal: ["▏", "▎", "▍", "▌", "▋", "▊", "▉", "▊", "▋", "▌", "▍", "▎"],
  balloon: [".", "o", "O", "@", "*"],
  balloon2: [".", "o", "O", "°", "O", "o", "."],
  noise: ["▓", "▒", "░"],
  bounce: ["( ", "o ", "O ", "o ", " (", "  ", "  )", " o", " O", " o", "( ", "  "],
  boxBounce: ["▖", "▘", "▝", "▗"],
  weather: ["☀️", "⛅", "☁️", "🌧️", "⛈️", "❄️"],
  moon: ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"],
  runner: ["🚶", "🏃"],
  pong: [
    "▐⠂       ▌",
    "▐⠈       ▌",
    "▐ ⠂      ▌",
    "▐ ⠠      ▌",
    "▐  ⡀     ▌",
    "▐  ⠠     ▌",
    "▐  ⠂     ▌",
    "▐ ⠈      ▌",
    "▐ ⠂      ▌",
    "▐⠠       ▌",
    "▐⡀       ▌",
    "▐⠠       ▌",
    "▐⠂       ▌",
  ],
  shark: [
    "▐|\____________▌",
    "▐_|\___________▌",
    "▐__|\__________▌",
    "▐___|\_________▌",
    "▐____|\________▌",
    "▐_____|\_______▌",
    "▐______|\______▌",
    "▐_______|\_____▌",
    "▐________|\____▌",
    "▐_________|\___▌",
    "▐__________|\__▌",
    "▐___________|\_▌",
    "▐____________|\▌",
    "▐____________/|▌",
    "▐___________/|_▌",
    "▐__________/|__▌",
    "▐_________/|___▌",
    "▐________/|____▌",
    "▐_______/|_____▌",
    "▐______/|______▌",
    "▐_____/|_______▌",
    "▐____/|________▌",
    "▐___/|_________▌",
    "▐__/|__________▌",
    "▐_/|___________▌",
    "▐/|____________▌",
  ],
  dqpb: ["d", "q", "p", "b"],
} as const;

export type SpinnerName = keyof typeof spinnerFrames;

// Color type definition
export type ColorInput =
  | string
  | { r: number; g: number; b: number }
  | { h: number; s: number; l: number };

export type ColorGenerator = (
  frameIndex: number,
  charIndex: number,
  totalFrames: number,
  totalChars: number
) => ColorInput;

// Zod schema for spinner props
export const SpinnerPropsSchema = z.object({
  name: z.enum(Object.keys(spinnerFrames) as [SpinnerName, ...SpinnerName[]]).optional(),
  frames: z.array(z.string()).optional(),
  interval: z.number().positive().optional(),
  autoplay: z.boolean().optional(),
  color: z.union([z.string(), z.custom<ColorGenerator>(() => true)]).optional(),
  backgroundColor: z.string().optional(),
  text: z.string().optional(),
});

export type SpinnerProps = {
  name?: SpinnerName;
  frames?: string[];
  interval?: number;
  autoplay?: boolean;
  color?: ColorInput | ColorGenerator;
  backgroundColor?: string;
  text?: string;
};

// Built-in color generators
export function createPulse(colors: ColorInput[], speed: number = 1): ColorGenerator {
  return (frameIndex) => {
    const index = Math.floor(frameIndex * speed) % colors.length;
    return colors[index] ?? colors[0] ?? "white";
  };
}

export function createWave(colors: ColorInput[]): ColorGenerator {
  return (frameIndex, charIndex, _totalFrames, _totalChars) => {
    const waveIndex = (frameIndex + charIndex) % colors.length;
    return colors[waveIndex] ?? colors[0] ?? "white";
  };
}

// Helper to convert color to ANSI
function colorToAnsi(color: ColorInput): string {
  if (typeof color === "string") {
    // Named colors mapping
    const namedColors: Record<string, string> = {
      black: "\x1b[30m",
      red: "\x1b[31m",
      green: "\x1b[32m",
      yellow: "\x1b[33m",
      blue: "\x1b[34m",
      magenta: "\x1b[35m",
      cyan: "\x1b[36m",
      white: "\x1b[37m",
      gray: "\x1b[90m",
      brightRed: "\x1b[91m",
      brightGreen: "\x1b[92m",
      brightYellow: "\x1b[93m",
      brightBlue: "\x1b[94m",
      brightMagenta: "\x1b[95m",
      brightCyan: "\x1b[96m",
      brightWhite: "\x1b[97m",
    };

    if (namedColors[color]) {
      return namedColors[color];
    }

    // Hex color
    if (color.startsWith("#")) {
      const hex = color.slice(1);
      const r = parseInt(hex.slice(0, 2), 16);
      const g = parseInt(hex.slice(2, 4), 16);
      const b = parseInt(hex.slice(4, 6), 16);
      return `\x1b[38;2;${r};${g};${b}m`;
    }

    return "";
  }

  if ("r" in color) {
    // RGB
    return `\x1b[38;2;${color.r};${color.g};${color.b}m`;
  }

  // HSL - convert to RGB (simplified)
  const { h, s, l } = color;
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;

  let r, g, b;
  if (h < 60) {
    [r, g, b] = [c, x, 0];
  } else if (h < 120) {
    [r, g, b] = [x, c, 0];
  } else if (h < 180) {
    [r, g, b] = [0, c, x];
  } else if (h < 240) {
    [r, g, b] = [0, x, c];
  } else if (h < 300) {
    [r, g, b] = [x, 0, c];
  } else {
    [r, g, b] = [c, 0, x];
  }

  return `\x1b[38;2;${Math.round((r + m) * 255)};${Math.round((g + m) * 255)};${Math.round((b + m) * 255)}m`;
}

export function Spinner({
  name = "dots",
  frames: customFrames,
  interval = 80,
  autoplay = true,
  color = "white",
  backgroundColor,
  text,
}: SpinnerProps) {
  const [frame, setFrame] = useState(0);
  const frames = customFrames || spinnerFrames[name];
  const currentFrame = frames[frame % frames.length] ?? frames[0] ?? " ";

  useEffect(() => {
    if (!autoplay) return;

    const timer = setInterval(() => {
      setFrame((f) => (f + 1) % frames.length);
    }, interval);

    return () => clearInterval(timer);
  }, [autoplay, interval, frames.length]);

  // Calculate color for current frame
  let frameColor: string;
  const colorStr = color as ColorInput;

  if (typeof color === "function") {
    // If it's a color generator, apply to each char
    const colorFn = color as ColorGenerator;
    const coloredChars = currentFrame.split("").map((char, i) => {
      const c = colorFn(frame, i, frames.length, currentFrame.length);
      const ansi = colorToAnsi(c);
      return `${ansi}${char}\x1b[0m`;
    });
    frameColor = coloredChars.join("");
  } else {
    const ansi = colorToAnsi(colorStr);
    frameColor = `${ansi}${currentFrame}\x1b[0m`;
  }

  return (
    <text>
      {frameColor}
      {text && <span> {text}</span>}
    </text>
  );
}

export default Spinner;

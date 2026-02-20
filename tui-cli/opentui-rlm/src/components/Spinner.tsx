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

  const resolvedColor = typeof color === "function"
    ? color(frame, 0, frames.length, 1)
    : color;

  const colorStr = typeof resolvedColor === "string"
    ? resolvedColor
    : resolvedColor && "r" in resolvedColor
      ? `rgb(${resolvedColor.r}, ${resolvedColor.g}, ${resolvedColor.b})`
      : resolvedColor && "h" in resolvedColor
        ? `hsl(${resolvedColor.h}, ${resolvedColor.s * 100}%, ${resolvedColor.l * 100}%)`
        : "white";

  return (
    <text fg={colorStr} bg={backgroundColor}>
      {currentFrame}
      {text && <span> {text}</span>}
    </text>
  );
}

export default Spinner;

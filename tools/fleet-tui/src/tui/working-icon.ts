/** Shared "still working" pulse indicator for in-progress markers. */

export const WORKING_ICON_FRAMES = ["◇", "◈", "◆", "◈"] as const;
export const WORKING_ICON_INTERVAL_MS = 250;

export function workingIconFrame(frame: number): string {
  const frames = WORKING_ICON_FRAMES;
  return frames[((frame % frames.length) + frames.length) % frames.length] ?? frames[0];
}

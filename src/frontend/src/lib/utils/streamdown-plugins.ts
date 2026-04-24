import { useSyncExternalStore } from "react";
import { cjk } from "@streamdown/cjk";
import { code } from "@streamdown/code";
import { math } from "@streamdown/math";

type Plugins = Record<string, unknown>;

let plugins: Plugins = { cjk, code, math };
const listeners = new Set<() => void>();

// Kick off mermaid load immediately — non-blocking, splits it from the main chunk.
// All MessageResponse/ReasoningContent instances re-render once when it resolves.
import("@streamdown/mermaid").then(({ mermaid }) => {
  plugins = { ...plugins, mermaid };
  listeners.forEach((fn) => fn());
});

const subscribe = (cb: () => void) => {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
};

const getSnapshot = () => plugins;

// SSR/test environments get the initial plugin set (no mermaid) synchronously.
const getServerSnapshot = () => ({ cjk, code, math }) as Plugins;

export function useStreamdownPlugins(): Plugins {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

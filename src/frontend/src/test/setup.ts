/**
 * Global Vitest setup file.
 *
 * Runs once before each test file in the jsdom environment.
 * Keep this file lean — only add setup that is truly global.
 */
import { beforeEach } from "vite-plus/test";

// -- Web Storage ----------------------------------------------------------
// Reset between test files to avoid state leakage.
// Guard: a test may replace localStorage/sessionStorage with a spy object
// that doesn't implement .clear — in that case silently skip the reset.
beforeEach(() => {
  try {
    localStorage.clear();
  } catch {
    // intentional: storage may be mocked without .clear
  }
  try {
    sessionStorage.clear();
  } catch {
    // intentional: storage may be mocked without .clear
  }
});

// -- window.matchMedia ----------------------------------------------------
// jsdom doesn't implement matchMedia. Stub it so components that read
// prefers-color-scheme or breakpoints don't throw.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string): MediaQueryList => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

// -- ResizeObserver -------------------------------------------------------
// Required by several Radix UI / layout components.
globalThis.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};

// -- IntersectionObserver -------------------------------------------------
globalThis.IntersectionObserver = class MockIntersectionObserver {
  readonly root: Element | Document | null = null;
  readonly rootMargin: string = "";
  readonly thresholds: ReadonlyArray<number> = [];
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
} as unknown as typeof IntersectionObserver;

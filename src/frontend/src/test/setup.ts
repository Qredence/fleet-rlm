/**
 * Global Vitest setup file.
 *
 * Runs once before each test file in the jsdom environment.
 * Keep this file lean — only add setup that is truly global.
 */
import { beforeEach } from "vite-plus/test";

function createMemoryStorage(): Storage {
  const data = new Map<string, string>();

  return {
    get length() {
      return data.size;
    },
    clear() {
      data.clear();
    },
    getItem(key: string) {
      return data.has(key) ? data.get(key)! : null;
    },
    key(index: number) {
      return Array.from(data.keys())[index] ?? null;
    },
    removeItem(key: string) {
      data.delete(key);
    },
    setItem(key: string, value: string) {
      data.set(key, String(value));
    },
  };
}

function ensureStorage(name: "localStorage" | "sessionStorage") {
  const existing = globalThis[name] as Partial<Storage> | undefined;
  if (
    existing &&
    typeof existing.clear === "function" &&
    typeof existing.getItem === "function" &&
    typeof existing.key === "function" &&
    typeof existing.removeItem === "function" &&
    typeof existing.setItem === "function"
  ) {
    return;
  }

  Object.defineProperty(globalThis, name, {
    configurable: true,
    value: createMemoryStorage(),
  });
}

ensureStorage("localStorage");
ensureStorage("sessionStorage");

// -- Web Storage ----------------------------------------------------------
// Reset between test files to avoid state leakage.
// Guard: a test may replace localStorage/sessionStorage with a spy object
// that doesn't implement .clear — in that case silently skip the reset.
beforeEach(() => {
  ensureStorage("localStorage");
  ensureStorage("sessionStorage");
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

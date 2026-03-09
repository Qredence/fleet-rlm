import { beforeEach, describe, expect, it, vi } from "vitest";

function createMemoryStorage(): Storage {
  const values = new Map<string, string>();

  return {
    get length() {
      return values.size;
    },
    clear: () => {
      values.clear();
    },
    getItem: (key) => values.get(key) ?? null,
    key: (index) => Array.from(values.keys())[index] ?? null,
    removeItem: (key) => {
      values.delete(key);
    },
    setItem: (key, value) => {
      values.set(key, value);
    },
  };
}

describe("useThemeStore", () => {
  beforeEach(() => {
    vi.resetModules();
    const storage = createMemoryStorage();
    vi.stubGlobal("localStorage", storage);
    Object.defineProperty(window, "localStorage", {
      value: storage,
      configurable: true,
    });
    document.documentElement.classList.remove("dark");
    document.documentElement.style.colorScheme = "";
  });

  it("hydrates the previous theme key into the new persisted store", async () => {
    localStorage.setItem("theme", "dark");

    const { useThemeStore } = await import("@/stores/themeStore");

    await useThemeStore.persist.rehydrate();

    expect(useThemeStore.getState().isDark).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.style.colorScheme).toBe("dark");
  });
});

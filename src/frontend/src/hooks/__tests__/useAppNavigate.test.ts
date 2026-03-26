import { describe, expect, it } from "vite-plus/test";

import { navToPath, pathToNav } from "@/hooks/useAppNavigate";

describe("useAppNavigate route truth", () => {
  it("maps supported nav items to canonical routes", () => {
    expect(navToPath("workspace")).toBe("/app/workspace");
    expect(navToPath("volumes")).toBe("/app/volumes");
    expect(navToPath("settings")).toBe("/app/settings");
  });

  it("maps canonical app routes back to supported nav items", () => {
    expect(pathToNav("/app")).toBe("workspace");
    expect(pathToNav("/app/workspace")).toBe("workspace");
    expect(pathToNav("/app/volumes")).toBe("volumes");
    expect(pathToNav("/app/settings")).toBe("settings");
  });

  it("treats retired product routes as unsupported", () => {
    expect(pathToNav("/app/taxonomy")).toBeNull();
    expect(pathToNav("/app/taxonomy/demo-skill")).toBeNull();
    expect(pathToNav("/app/skills")).toBeNull();
    expect(pathToNav("/app/skills/demo-skill")).toBeNull();
    expect(pathToNav("/app/memory")).toBeNull();
    expect(pathToNav("/app/analytics")).toBeNull();
  });

  it("ignores paths outside the app shell", () => {
    expect(pathToNav("/login")).toBeNull();
    expect(pathToNav("/settings")).toBeNull();
  });
});

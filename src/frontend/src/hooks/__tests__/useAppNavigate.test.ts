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

  it("treats legacy or retired routes as redirects into the supported shell", () => {
    expect(pathToNav("/app/taxonomy")).toBe("volumes");
    expect(pathToNav("/app/taxonomy/demo-skill")).toBe("volumes");
    expect(pathToNav("/app/skills")).toBe("workspace");
    expect(pathToNav("/app/skills/demo-skill")).toBe("workspace");
    expect(pathToNav("/app/memory")).toBe("workspace");
    expect(pathToNav("/app/analytics")).toBe("workspace");
  });

  it("ignores paths outside the app shell", () => {
    expect(pathToNav("/login")).toBeNull();
    expect(pathToNav("/settings")).toBeNull();
  });
});

import { describe, expect, it } from "vitest";

import { parseArgs, run } from "../cli.js";

describe("CLI options", () => {
  it("uses the local API by default and accepts a resumed session", () => {
    expect(parseArgs(["--", "--session", "session-id"])).toEqual({
      apiUrl: "http://127.0.0.1:8000",
      sessionId: "session-id",
    });
  });

  it("rejects an option without a value", () => {
    expect(() => parseArgs(["--api-url"])).toThrow("Missing value");
  });
  it("rejects the removed classic renderer flag", () => {
    expect(() => parseArgs(["--classic"])).toThrow("Missing value for --classic");
  });

  it("rejects interactive chat when either stream is not a TTY", async () => {
    await expect(run({ apiUrl: "http://fleet.test" })).rejects.toThrow(
      "requires interactive stdin and stdout terminals",
    );
  });
});

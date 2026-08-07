import { createHash, randomUUID } from "node:crypto";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { FleetApiClient } from "../fleet-api-client.js";
import { saveArtifact } from "../cli-core.js";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function payloadResponse(payload: string, headers: Record<string, string>): Response {
  return new Response(Buffer.from(payload, "utf8"), { headers });
}

describe("saveArtifact", () => {
  it("downloads, verifies the ETag sha256 + Content-Length, and writes atomically", async () => {
    const payload = "# Report\nHello\n";
    const digest = createHash("sha256").update(payload).digest("hex");
    globalThis.fetch = vi.fn().mockResolvedValue(
      payloadResponse(payload, {
        "content-length": String(Buffer.byteLength(payload)),
        etag: `"${digest}"`,
      }),
    );

    const directory = await mkdtemp(join(tmpdir(), "fleet-artifact-"));
    const output = join(directory, "report.md");
    try {
      await saveArtifact(
        new FleetApiClient({ baseUrl: "http://fleet.test" }),
        randomUUID(),
        output,
      );
      expect(await readFile(output, "utf8")).toBe(payload);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("accepts a weak ETag (W/ prefix) by stripping it", async () => {
    const payload = "ok";
    const digest = createHash("sha256").update(payload).digest("hex");
    globalThis.fetch = vi.fn().mockResolvedValue(
      payloadResponse(payload, {
        "content-length": "2",
        etag: `W/"${digest}"`,
      }),
    );

    const directory = await mkdtemp(join(tmpdir(), "fleet-artifact-"));
    const output = join(directory, "x.txt");
    try {
      await saveArtifact(
        new FleetApiClient({ baseUrl: "http://fleet.test" }),
        randomUUID(),
        output,
      );
      expect(await readFile(output, "utf8")).toBe(payload);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("rejects a response missing the integrity headers", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(payloadResponse("data", {}));

    const directory = await mkdtemp(join(tmpdir(), "fleet-artifact-"));
    const output = join(directory, "x.txt");
    try {
      await expect(
        saveArtifact(new FleetApiClient({ baseUrl: "http://fleet.test" }), randomUUID(), output),
      ).rejects.toThrow("missing integrity headers");
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("rejects a body whose length or digest mismatches the headers and cleans up", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      payloadResponse("actual", {
        "content-length": "999",
        etag: `"${"f".repeat(64)}"`,
      }),
    );

    const directory = await mkdtemp(join(tmpdir(), "fleet-artifact-"));
    const output = join(directory, "x.txt");
    try {
      await expect(
        saveArtifact(new FleetApiClient({ baseUrl: "http://fleet.test" }), randomUUID(), output),
      ).rejects.toThrow("integrity verification failed");
      await expect(readFile(output, "utf8")).rejects.toThrow();
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
});

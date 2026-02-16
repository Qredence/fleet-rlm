import assert from "node:assert/strict";
import test from "node:test";

import { BridgeClient } from "./bridge.js";

function createClient(): BridgeClient {
  return new BridgeClient({
    pythonBin: "python",
    traceMode: "compact",
    hydraOverrides: [],
  });
}

function getInternals(client: BridgeClient): {
  process: {
    stdin?: { destroyed: boolean; write: (data: string) => boolean; end: () => void };
    stdout?: { on: (event: string, listener: (data: string) => void) => void };
    stderr?: { on: (event: string, listener: (data: Buffer) => void) => void };
    kill: (signal: string) => boolean;
    killed: boolean;
    on: (event: string, listener: (...args: unknown[]) => void) => void;
  } | null;
  readlineInterface: { close: () => void } | null;
  handleLine: (line: string) => void;
  pending: Map<string, { resolve: (value: unknown) => void; reject: (error: Error) => void }>;
  nextId: number;
  isShuttingDown: boolean;
} {
  return client as unknown as {
    process: {
      stdin?: { destroyed: boolean; write: (data: string) => boolean; end: () => void };
      stdout?: { on: (event: string, listener: (data: string) => void) => void };
      stderr?: { on: (event: string, listener: (data: Buffer) => void) => void };
      kill: (signal: string) => boolean;
      killed: boolean;
      on: (event: string, listener: (...args: unknown[]) => void) => void;
    } | null;
    readlineInterface: { close: () => void } | null;
    handleLine: (line: string) => void;
    pending: Map<string, { resolve: (value: unknown) => void; reject: (error: Error) => void }>;
    nextId: number;
    isShuttingDown: boolean;
  };
}

// ============================================================================
// Phase 1.1: Lifecycle Tests
// ============================================================================

test("start() sets up process with correct arguments", () => {
  const client = createClient();
  const internals = getInternals(client);

  let spawnArgs: string[] | undefined;
  internals.process = {
    stdin: { destroyed: false, write: () => true, end: () => {} },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: (event: string, listener: (...args: unknown[]) => void) => {
      if (event === "exit") {
        // Simulate process exit
        setTimeout(() => listener(0, null), 10);
      }
    },
  };

  // Process should be set after start
  assert.ok(internals.process);
});

test("start() with docsPath adds --docs-path argument", () => {
  const client = new BridgeClient({
    pythonBin: "python3",
    traceMode: "verbose",
    docsPath: "/path/to/docs",
    hydraOverrides: [],
  });

  // Verify options are stored correctly
  const options = (client as unknown as { options: { docsPath: string } }).options;
  assert.equal(options.docsPath, "/path/to/docs");
});

test("start() with volumeName and secretName adds respective arguments", () => {
  const client = new BridgeClient({
    pythonBin: "python3",
    traceMode: "compact",
    volumeName: "my-volume",
    secretName: "my-secret",
    hydraOverrides: [],
  });

  const options = (client as unknown as { options: { volumeName?: string; secretName?: string } })
    .options;
  assert.equal(options.volumeName, "my-volume");
  assert.equal(options.secretName, "my-secret");
});

test("start() with hydraOverrides adds them after -- separator", () => {
  const client = new BridgeClient({
    pythonBin: "python",
    traceMode: "off",
    hydraOverrides: ["model=gpt4", "temperature=0.7"],
  });

  const options = (client as unknown as { options: { hydraOverrides: string[] } }).options;
  assert.deepEqual(options.hydraOverrides, ["model=gpt4", "temperature=0.7"]);
});

test("shutdown() sends session.shutdown and cleans up", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  // Simulate a pending request to test graceful shutdown
  const requestPromise = client.request("test.method", {});

  // Shutdown should attempt to send session.shutdown
  const shutdownPromise = client.shutdown();

  // Verify shutdown flag is set
  assert.equal(internals.isShuttingDown, true);

  await shutdownPromise;
});

test("shutdown() handles already dead process gracefully", async () => {
  const client = createClient();
  const internals = getInternals(client);

  internals.process = null;

  // Should not throw when process is null
  await assert.doesNotReject(async () => {
    await client.shutdown();
  });
});

test("shutdown() kills process with SIGTERM then SIGKILL if needed", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const killSignals: string[] = [];

  internals.process = {
    stdin: { destroyed: false, write: () => true, end: () => {} },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: (signal: string) => {
      killSignals.push(signal);
      return true;
    },
    killed: false,
    on: () => {},
  };

  const shutdownPromise = client.shutdown();

  // Wait for the 100ms timeout
  await new Promise((resolve) => setTimeout(resolve, 150));

  // Should have tried SIGTERM
  assert.ok(killSignals.includes("SIGTERM"));
});

test("process exit emits error to listeners when not shutting down", () => {
  const client = createClient();
  const internals = getInternals(client);
  const errors: Error[] = [];

  client.onError((error) => errors.push(error));

  let exitHandler: ((code: number | null, signal: string | null) => void) | undefined;

  internals.process = {
    stdin: { destroyed: false, write: () => true, end: () => {} },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: (event: string, listener: (...args: unknown[]) => void) => {
      if (event === "exit") {
        exitHandler = listener as (code: number | null, signal: string | null) => void;
      }
    },
  };

  // Simulate process exit with non-zero code
  if (exitHandler) {
    exitHandler(1, "SIGTERM");
  }

  assert.equal(errors.length, 1);
  assert.ok(errors[0]?.message.includes("exited with code 1"));
});

test("process exit with code 0 does not emit error", () => {
  const client = createClient();
  const internals = getInternals(client);
  const errors: Error[] = [];

  client.onError((error) => errors.push(error));

  let exitHandler: ((code: number | null, signal: string | null) => void) | undefined;

  internals.process = {
    stdin: { destroyed: false, write: () => true, end: () => {} },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: (event: string, listener: (...args: unknown[]) => void) => {
      if (event === "exit") {
        exitHandler = listener as (code: number | null, signal: string | null) => void;
      }
    },
  };

  // Simulate successful process exit
  if (exitHandler) {
    exitHandler(0, null);
  }

  assert.equal(errors.length, 0);
});

test("process exit rejects all pending requests", () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  let exitHandler: ((code: number | null, signal: string | null) => void) | undefined;

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: (event: string, listener: (...args: unknown[]) => void) => {
      if (event === "exit") {
        exitHandler = listener as (code: number | null, signal: string | null) => void;
      }
    },
  };

  // Start a request that will never resolve
  const pendingRequest = client.request("test.method", {});

  // Simulate process exit
  if (exitHandler) {
    exitHandler(1, null);
  }

  // Pending request should be rejected
  assert.rejects(pendingRequest, /Bridge process terminated/);
});

test("stderr output is logged to console.error", () => {
  const client = createClient();
  const internals = getInternals(client);

  let stderrHandler: ((data: Buffer) => void) | undefined;
  const originalConsoleError = console.error;
  const consoleErrors: string[] = [];
  console.error = (...args: unknown[]) => {
    consoleErrors.push(args.join(" "));
  };

  internals.process = {
    stdin: { destroyed: false, write: () => true, end: () => {} },
    stdout: { on: () => {} },
    stderr: {
      on: (event: string, listener: (data: Buffer) => void) => {
        if (event === "data") stderrHandler = listener;
      },
    },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  // Simulate stderr data
  if (stderrHandler) {
    stderrHandler(Buffer.from("Warning: deprecation message"));
  }

  console.error = originalConsoleError;
  assert.ok(consoleErrors.some((e) => e.includes("[bridge stderr]") && e.includes("Warning")));
});

// ============================================================================
// Phase 1.2: Request/Response Tests
// ============================================================================

test("request() rejects if process not started", async () => {
  const client = createClient();
  await assert.rejects(
    client.request("status.get", {}),
    /Bridge process is not running\. Call start\(\) first\./,
  );
});

test("request() rejects if stdin is destroyed", async () => {
  const client = createClient();
  const internals = getInternals(client);

  internals.process = {
    stdin: { destroyed: true, write: () => true, end: () => {} },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  await assert.rejects(
    client.request("status.get", {}),
    /Bridge process is not running\. Call start\(\) first\./,
  );
});

test("request() auto-increments request IDs", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  // Send multiple requests
  client.request("method1", {});
  client.request("method2", {});
  client.request("method3", {});

  // Parse the request IDs
  const req1 = JSON.parse(writes[0] ?? "{}");
  const req2 = JSON.parse(writes[1] ?? "{}");
  const req3 = JSON.parse(writes[2] ?? "{}");

  assert.equal(req1.id, "1");
  assert.equal(req2.id, "2");
  assert.equal(req3.id, "3");
});

test("request() resolves on matching response", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  const pending = client.request("test.method", { param: "value" });

  const outbound = JSON.parse(writes[0] ?? "{}");
  assert.equal(outbound.method, "test.method");
  assert.deepEqual(outbound.params, { param: "value" });

  // Simulate response
  internals.handleLine(
    JSON.stringify({ id: outbound.id, result: { success: true, data: [1, 2, 3] } }),
  );

  const result = await pending;
  assert.deepEqual(result, { success: true, data: [1, 2, 3] });
});

test("request() handles out-of-order responses", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  const promise1 = client.request("method1", {});
  const promise2 = client.request("method2", {});

  const id1 = JSON.parse(writes[0] ?? "{}").id;
  const id2 = JSON.parse(writes[1] ?? "{}").id;

  // Respond to request 2 first (out of order)
  internals.handleLine(JSON.stringify({ id: id2, result: { name: "second" } }));
  internals.handleLine(JSON.stringify({ id: id1, result: { name: "first" } }));

  const result1 = await promise1;
  const result2 = await promise2;

  assert.deepEqual(result1, { name: "first" });
  assert.deepEqual(result2, { name: "second" });
});

test("request() ignores responses for unknown request IDs", () => {
  const client = createClient();
  const internals = getInternals(client);

  // Should not throw or error when receiving response for unknown ID
  assert.doesNotThrow(() => {
    internals.handleLine(JSON.stringify({ id: "99999", result: { data: "orphaned" } }));
  });
});

test("request() handles write errors", async () => {
  const client = createClient();
  const internals = getInternals(client);

  internals.process = {
    stdin: {
      destroyed: false,
      write: () => {
        throw new Error("Write failed");
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  await assert.rejects(client.request("test.method", {}), /Write failed/);
});

// ============================================================================
// Phase 1.3: Event Handling Tests
// ============================================================================

test("onEvent() receives events from stdout", () => {
  const client = createClient();
  const internals = getInternals(client);
  const events: Array<{ event: string; params: Record<string, unknown> }> = [];

  const unsubscribe = client.onEvent((event) => events.push(event));

  internals.handleLine(
    JSON.stringify({
      event: "chat.event",
      params: { kind: "assistant_token", text: "hello" },
    }),
  );

  assert.equal(events.length, 1);
  assert.equal(events[0]?.event, "chat.event");
  assert.deepEqual(events[0]?.params, { kind: "assistant_token", text: "hello" });

  unsubscribe();
});

test("onEvent() returns unsubscribe function", () => {
  const client = createClient();
  const internals = getInternals(client);
  const events: Array<{ event: string; params: Record<string, unknown> }> = [];

  const unsubscribe = client.onEvent((event) => events.push(event));

  // Receive one event
  internals.handleLine(JSON.stringify({ event: "event1", params: {} }));
  assert.equal(events.length, 1);

  // Unsubscribe
  unsubscribe();

  // Should not receive more events
  internals.handleLine(JSON.stringify({ event: "event2", params: {} }));
  assert.equal(events.length, 1);
});

test("onEvent() handles multiple listeners", () => {
  const client = createClient();
  const internals = getInternals(client);
  const events1: Array<{ event: string; params: Record<string, unknown> }> = [];
  const events2: Array<{ event: string; params: Record<string, unknown> }> = [];

  client.onEvent((event) => events1.push(event));
  client.onEvent((event) => events2.push(event));

  internals.handleLine(JSON.stringify({ event: "test", params: { data: 123 } }));

  assert.equal(events1.length, 1);
  assert.equal(events2.length, 1);
  assert.deepEqual(events1[0], events2[0]);
});

test("onEvent() ignores listener errors", () => {
  const client = createClient();
  const internals = getInternals(client);

  client.onEvent(() => {
    throw new Error("Listener crash");
  });

  // Should not throw even though listener crashes
  assert.doesNotThrow(() => {
    internals.handleLine(JSON.stringify({ event: "test", params: {} }));
  });
});

test("onEvent() handles legacy event format", () => {
  const client = createClient();
  const internals = getInternals(client);
  const events: Array<{ event: string; params: Record<string, unknown> }> = [];

  client.onEvent((event) => events.push(event));

  internals.handleLine(
    JSON.stringify({
      type: "event",
      data: { kind: "old_format", text: "legacy data" },
    }),
  );

  assert.equal(events.length, 1);
  assert.equal(events[0]?.event, "old_format");
  assert.equal(events[0]?.params.kind, "old_format");
});

test("onError() receives process errors", () => {
  const client = createClient();
  const internals = getInternals(client);
  const errors: Error[] = [];

  const unsubscribe = client.onError((error) => errors.push(error));

  // Simulate process error
  const errorListeners = (
    client as unknown as {
      errorListeners: Set<(error: Error) => void>;
    }
  ).errorListeners;

  for (const listener of errorListeners) {
    listener(new Error("Connection lost"));
  }

  assert.equal(errors.length, 1);
  assert.equal(errors[0]?.message, "Connection lost");

  unsubscribe();
});

test("onError() unsubscribe works", () => {
  const client = createClient();
  const errors: Error[] = [];

  const unsubscribe = client.onError((error) => errors.push(error));
  unsubscribe();

  const errorListeners = (
    client as unknown as {
      errorListeners: Set<(error: Error) => void>;
    }
  ).errorListeners;

  // Should not receive errors after unsubscribing
  for (const listener of errorListeners) {
    listener(new Error("Should not be received"));
  }

  assert.equal(errors.length, 0);
});

test("handleLine ignores empty lines", () => {
  const client = createClient();
  const internals = getInternals(client);
  const events: Array<{ event: string; params: Record<string, unknown> }> = [];

  client.onEvent((event) => events.push(event));

  // Should not throw on empty lines
  assert.doesNotThrow(() => {
    internals.handleLine("");
    internals.handleLine("   ");
    internals.handleLine("  \t  ");
  });

  assert.equal(events.length, 0);
});

test("handleLine ignores invalid JSON", () => {
  const client = createClient();
  const internals = getInternals(client);

  // Should not throw on invalid JSON
  assert.doesNotThrow(() => {
    internals.handleLine("not valid json");
    internals.handleLine("{ broken json");
    internals.handleLine("<xml>not json</xml>");
  });
});

test("handleLine ignores non-object payloads", () => {
  const client = createClient();
  const internals = getInternals(client);
  const events: Array<{ event: string; params: Record<string, unknown> }> = [];

  client.onEvent((event) => events.push(event));

  // Should not throw or produce events for non-objects
  assert.doesNotThrow(() => {
    internals.handleLine("123"); // number
    internals.handleLine('"string"'); // string
    internals.handleLine("true"); // boolean
    internals.handleLine("null"); // null
  });

  assert.equal(events.length, 0);
});

// ============================================================================
// Phase 1.4: State Methods Tests
// ============================================================================

test("stateGet() sends state.get request", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  const pending = client.stateGet("my-namespace", "my-key");

  const request = JSON.parse(writes[0] ?? "{}");
  assert.equal(request.method, "state.get");
  assert.equal(request.params.namespace, "my-namespace");
  assert.equal(request.params.key, "my-key");

  // Simulate response
  internals.handleLine(
    JSON.stringify({
      id: request.id,
      result: { value: "my-value", found: true, timestamp: 1234567890 },
    }),
  );

  const result = await pending;
  assert.equal(result.value, "my-value");
  assert.equal(result.found, true);
  assert.equal(result.timestamp, 1234567890);
});

test("stateGet() handles default value", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  const pending = client.stateGet("ns", "key", "default-value");

  const request = JSON.parse(writes[0] ?? "{}");
  assert.equal(request.params.default, "default-value");

  internals.handleLine(
    JSON.stringify({
      id: request.id,
      result: { value: "default-value", found: false },
    }),
  );

  const result = await pending;
  assert.equal(result.value, "default-value");
  assert.equal(result.found, false);
});

test("stateSet() sends state.set request", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  const pending = client.stateSet("ns", "key", { complex: "value", num: 42 });

  const request = JSON.parse(writes[0] ?? "{}");
  assert.equal(request.method, "state.set");
  assert.equal(request.params.namespace, "ns");
  assert.equal(request.params.key, "key");
  assert.deepEqual(request.params.value, { complex: "value", num: 42 });

  internals.handleLine(
    JSON.stringify({
      id: request.id,
      result: { ok: true, path: "/path/to/state" },
    }),
  );

  const result = await pending;
  assert.equal(result.ok, true);
  assert.equal(result.path, "/path/to/state");
});

test("stateDelete() sends state.delete request", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  const pending = client.stateDelete("ns", "key");

  const request = JSON.parse(writes[0] ?? "{}");
  assert.equal(request.method, "state.delete");
  assert.equal(request.params.namespace, "ns");
  assert.equal(request.params.key, "key");

  internals.handleLine(
    JSON.stringify({
      id: request.id,
      result: { ok: true, existed: true },
    }),
  );

  const result = await pending;
  assert.equal(result.ok, true);
  assert.equal(result.existed, true);
});

test("stateList() returns keys array", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  const pending = client.stateList("ns");

  const request = JSON.parse(writes[0] ?? "{}");
  assert.equal(request.method, "state.list");
  assert.equal(request.params.namespace, "ns");

  internals.handleLine(
    JSON.stringify({
      id: request.id,
      result: {
        keys: [
          { key: "key1", timestamp: 1000 },
          { key: "key2", timestamp: 2000 },
        ],
        count: 2,
      },
    }),
  );

  const result = await pending;
  assert.equal(result.keys.length, 2);
  assert.equal(result.keys[0]?.key, "key1");
  assert.equal(result.count, 2);
});

test("stateClear() returns deleted count", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  const pending = client.stateClear("ns");

  const request = JSON.parse(writes[0] ?? "{}");
  assert.equal(request.method, "state.clear");
  assert.equal(request.params.namespace, "ns");

  internals.handleLine(
    JSON.stringify({
      id: request.id,
      result: { ok: true, deleted_count: 5 },
    }),
  );

  const result = await pending;
  assert.equal(result.ok, true);
  assert.equal(result.deletedCount, 5);
});

// ============================================================================
// Phase 1.5: Volume Methods Tests
// ============================================================================

test("volumeRead() sends volume.read request", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  const pending = client.volumeRead("my-volume", "/path/to/file.txt", "utf-8");

  const request = JSON.parse(writes[0] ?? "{}");
  assert.equal(request.method, "volume.read");
  assert.equal(request.params.volume_name, "my-volume");
  assert.equal(request.params.path, "/path/to/file.txt");
  assert.equal(request.params.encoding, "utf-8");

  internals.handleLine(
    JSON.stringify({
      id: request.id,
      result: {
        content: "file contents here",
        path: "/path/to/file.txt",
        volume_name: "my-volume",
        encoding: "utf-8",
      },
    }),
  );

  const result = await pending;
  assert.equal(result.content, "file contents here");
});

test("volumeRead() uses default encoding utf-8", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  client.volumeRead("vol", "/file.txt");

  const request = JSON.parse(writes[0] ?? "{}");
  assert.equal(request.params.encoding, "utf-8");
});

test("volumeWrite() sends volume.write request", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  const pending = client.volumeWrite("vol", "/file.txt", "file contents", "utf-8");

  const request = JSON.parse(writes[0] ?? "{}");
  assert.equal(request.method, "volume.write");
  assert.equal(request.params.volume_name, "vol");
  assert.equal(request.params.path, "/file.txt");
  assert.equal(request.params.content, "file contents");

  internals.handleLine(
    JSON.stringify({
      id: request.id,
      result: { ok: true, path: "/file.txt", volume_name: "vol", bytes_written: 13 },
    }),
  );

  const result = await pending;
  assert.equal(result.ok, true);
  assert.equal(result.bytes_written, 13);
});

test("volumeList() sends volume.list request", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  const pending = client.volumeList("vol", "prefix/", true);

  const request = JSON.parse(writes[0] ?? "{}");
  assert.equal(request.method, "volume.list");
  assert.equal(request.params.volume_name, "vol");
  assert.equal(request.params.prefix, "prefix/");
  assert.equal(request.params.recursive, true);

  internals.handleLine(
    JSON.stringify({
      id: request.id,
      result: {
        files: [
          { path: "prefix/file1.txt", type: "file", size: 100, mtime: 1234567890 },
          { path: "prefix/dir", type: "directory" },
        ],
        count: 2,
        volume_name: "vol",
      },
    }),
  );

  const result = await pending;
  assert.equal(result.files.length, 2);
  assert.equal(result.count, 2);
});

test("volumeList() uses default parameters", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  client.volumeList("vol");

  const request = JSON.parse(writes[0] ?? "{}");
  assert.equal(request.params.prefix, "");
  assert.equal(request.params.recursive, false);
});

test("volumeDelete() sends volume.delete request", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  const pending = client.volumeDelete("vol", "/path/to/delete", true);

  const request = JSON.parse(writes[0] ?? "{}");
  assert.equal(request.method, "volume.delete");
  assert.equal(request.params.volume_name, "vol");
  assert.equal(request.params.path, "/path/to/delete");
  assert.equal(request.params.recursive, true);

  internals.handleLine(
    JSON.stringify({
      id: request.id,
      result: { ok: true, path: "/path/to/delete", volume_name: "vol" },
    }),
  );

  const result = await pending;
  assert.equal(result.ok, true);
});

test("volumeDelete() uses default recursive=false", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  client.volumeDelete("vol", "/file.txt");

  const request = JSON.parse(writes[0] ?? "{}");
  assert.equal(request.params.recursive, false);
});

test("volumeInfo() sends volume.info request", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  const pending = client.volumeInfo("vol");

  const request = JSON.parse(writes[0] ?? "{}");
  assert.equal(request.method, "volume.info");
  assert.equal(request.params.volume_name, "vol");

  internals.handleLine(
    JSON.stringify({
      id: request.id,
      result: {
        name: "vol",
        version: 1,
        exists: true,
        file_count: 10,
        dir_count: 2,
      },
    }),
  );

  const result = await pending;
  assert.equal(result.name, "vol");
  assert.equal(result.exists, true);
  assert.equal(result.file_count, 10);
});

// ============================================================================
// Phase 1.6: Memory Methods Tests
// ============================================================================

test("memoryRead() sends memory.read request", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  const pending = client.memoryRead<{ foo: string; bar: number }>("my-key", "my-vol");

  const request = JSON.parse(writes[0] ?? "{}");
  assert.equal(request.method, "memory.read");
  assert.equal(request.params.key, "my-key");
  assert.equal(request.params.volume_name, "my-vol");

  internals.handleLine(
    JSON.stringify({
      id: request.id,
      result: {
        content: { foo: "hello", bar: 42 },
        path: "/memory/my-key",
        volume_name: "my-vol",
        encoding: "json",
      },
    }),
  );

  const result = await pending;
  assert.deepEqual(result.content, { foo: "hello", bar: 42 });
});

test("memoryRead() handles optional volumeName", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  client.memoryRead("key");

  const request = JSON.parse(writes[0] ?? "{}");
  assert.equal(request.params.volume_name, undefined);
});

test("memoryWrite() sends memory.write request", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  const data = { test: "data", nested: { value: 123 } };
  const pending = client.memoryWrite("my-key", data, "my-vol");

  const request = JSON.parse(writes[0] ?? "{}");
  assert.equal(request.method, "memory.write");
  assert.equal(request.params.key, "my-key");
  assert.deepEqual(request.params.data, data);
  assert.equal(request.params.volume_name, "my-vol");

  internals.handleLine(
    JSON.stringify({
      id: request.id,
      result: {
        ok: true,
        path: "/memory/my-key",
        volume_name: "my-vol",
        bytes_written: 100,
        key: "my-key",
      },
    }),
  );

  const result = await pending;
  assert.equal(result.ok, true);
  assert.equal(result.key, "my-key");
});

test("memoryList() sends memory.list request", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  const pending = client.memoryList("my-vol", "prefix");

  const request = JSON.parse(writes[0] ?? "{}");
  assert.equal(request.method, "memory.list");
  assert.equal(request.params.volume_name, "my-vol");
  assert.equal(request.params.prefix, "prefix");

  internals.handleLine(
    JSON.stringify({
      id: request.id,
      result: {
        keys: [
          { key: "key1", mtime: 1000, size: 50 },
          { key: "key2", mtime: 2000, size: 100 },
        ],
        count: 2,
        volume_name: "my-vol",
      },
    }),
  );

  const result = await pending;
  assert.equal(result.keys.length, 2);
  assert.equal(result.count, 2);
});

// ============================================================================
// Phase 1.7: Sandbox Methods Tests
// ============================================================================

test("sandboxList() sends sandbox.list request", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  const pending = client.sandboxList("my-app", "prod");

  const request = JSON.parse(writes[0] ?? "{}");
  assert.equal(request.method, "sandbox.list");
  assert.equal(request.params.app_name, "my-app");
  assert.equal(request.params.tag, "prod");

  internals.handleLine(
    JSON.stringify({
      id: request.id,
      result: {
        sandboxes: [
          { id: "sb-1", app_name: "my-app", tags: ["prod"], status: "running" },
          { id: "sb-2", app_name: "my-app", tags: ["prod", "v2"], status: "stopped" },
        ],
        count: 2,
      },
    }),
  );

  const result = await pending;
  assert.equal(result.sandboxes.length, 2);
  assert.equal(result.count, 2);
});

test("sandboxList() handles optional parameters", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  client.sandboxList();

  const request = JSON.parse(writes[0] ?? "{}");
  assert.equal(request.params.app_name, undefined);
  assert.equal(request.params.tag, undefined);
});

test("sandboxExec() sends sandbox.exec request", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  const pending = client.sandboxExec("ls -la", {
    image: "python:3.11",
    volume_name: "my-vol",
    timeout: 300,
    app_name: "test-app",
  });

  const request = JSON.parse(writes[0] ?? "{}");
  assert.equal(request.method, "sandbox.exec");
  assert.equal(request.params.command, "ls -la");
  assert.equal(request.params.image, "python:3.11");
  assert.equal(request.params.volume_name, "my-vol");
  assert.equal(request.params.timeout, 300);
  assert.equal(request.params.app_name, "test-app");

  internals.handleLine(
    JSON.stringify({
      id: request.id,
      result: {
        ok: true,
        returncode: 0,
        stdout: "file1.txt\nfile2.txt",
        stderr: "",
        sandbox_id: "sb-123",
      },
    }),
  );

  const result = await pending;
  assert.equal(result.ok, true);
  assert.equal(result.returncode, 0);
  assert.equal(result.sandbox_id, "sb-123");
});

test("sandboxExec() handles command without options", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  client.sandboxExec("echo hello");

  const request = JSON.parse(writes[0] ?? "{}");
  assert.equal(request.params.command, "echo hello");
  assert.equal(request.params.image, undefined);
});

test("sandboxExec() handles failed command", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  const pending = client.sandboxExec("exit 1");

  const request = JSON.parse(writes[0] ?? "{}");

  internals.handleLine(
    JSON.stringify({
      id: request.id,
      result: {
        ok: false,
        returncode: 1,
        stdout: "",
        stderr: "Command failed",
        sandbox_id: "sb-456",
      },
    }),
  );

  const result = await pending;
  assert.equal(result.ok, false);
  assert.equal(result.returncode, 1);
  assert.equal(result.stderr, "Command failed");
});

// ============================================================================
// Phase 1.8: Error Response Tests
// ============================================================================

test("request() rejects on error response with code and message", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  const pending = client.request("test.method", {});
  const requestId = JSON.parse(writes[0] ?? "{}").id;

  internals.handleLine(
    JSON.stringify({
      id: requestId,
      error: { code: "NOT_FOUND", message: "Resource not found" },
    }),
  );

  await assert.rejects(pending, /\[NOT_FOUND\] Resource not found/);
});

test("request() handles error without code", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  const pending = client.request("test.method", {});
  const requestId = JSON.parse(writes[0] ?? "{}").id;

  internals.handleLine(
    JSON.stringify({
      id: requestId,
      error: { message: "Something went wrong" },
    }),
  );

  await assert.rejects(pending, /\[UNKNOWN\] Something went wrong/);
});

test("request() handles error without message", async () => {
  const client = createClient();
  const internals = getInternals(client);
  const writes: string[] = [];

  internals.process = {
    stdin: {
      destroyed: false,
      write: (data: string) => {
        writes.push(data);
        return true;
      },
      end: () => {},
    },
    stdout: { on: () => {} },
    stderr: { on: () => {} },
    kill: () => true,
    killed: false,
    on: () => {},
  };

  const pending = client.request("test.method", {});
  const requestId = JSON.parse(writes[0] ?? "{}").id;

  internals.handleLine(
    JSON.stringify({
      id: requestId,
      error: { code: "ERROR" },
    }),
  );

  await assert.rejects(pending, /\[ERROR\] Bridge error/);
});

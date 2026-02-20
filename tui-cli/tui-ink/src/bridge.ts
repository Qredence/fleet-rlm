import { spawn, type ChildProcess } from "node:child_process";
import { createInterface, type Interface } from "node:readline";

import {
  BridgeEventEnvelopeSchema,
  BridgeLegacyEventEnvelopeSchema,
  BridgeResponseEnvelopeSchema,
  BridgeRpcErrorSchema,
  BridgeUnknownRecordSchema,
} from "./bridge-schemas.js";

export type BridgeEvent = {
  event: string;
  params: Record<string, unknown>;
};

export type BridgeClientOptions = {
  pythonBin: string;
  traceMode: "compact" | "verbose" | "off";
  docsPath?: string;
  volumeName?: string;
  secretName?: string;
  hydraOverrides: string[];
};

export class BridgeClient {
  private process: ChildProcess | null = null;
  private readlineInterface: Interface | null = null;
  private nextId = 1;
  private readonly pending = new Map<
    string,
    { resolve: (value: unknown) => void; reject: (error: Error) => void }
  >();
  private readonly eventListeners = new Set<(event: BridgeEvent) => void>();
  private readonly errorListeners = new Set<(error: Error) => void>();
  private isShuttingDown = false;

  public constructor(private readonly options: BridgeClientOptions) {}

  public start(): void {
    if (this.process) {
      return;
    }

    this.isShuttingDown = false;

    // Build command line arguments for the Python bridge server
    const args = ["-m", "fleet_rlm.bridge.server"];

    args.push("--trace-mode", this.options.traceMode);

    if (this.options.docsPath) {
      args.push("--docs-path", this.options.docsPath);
    }

    if (this.options.volumeName) {
      args.push("--volume-name", this.options.volumeName);
    }

    if (this.options.secretName) {
      args.push("--secret-name", this.options.secretName);
    }

    // Add hydra overrides after -- separator
    if (this.options.hydraOverrides.length > 0) {
      args.push("--");
      args.push(...this.options.hydraOverrides);
    }

    try {
      this.process = spawn(this.options.pythonBin, args, {
        stdio: ["pipe", "pipe", "pipe"],
      });
    } catch (error) {
      const err = error instanceof Error ? error : new Error(String(error));
      this.notifyError(err);
      throw err;
    }

    // Set up readline interface for line-by-line reading from stdout
    if (this.process.stdout) {
      this.readlineInterface = createInterface({
        input: this.process.stdout,
        crlfDelay: Number.POSITIVE_INFINITY,
      });

      this.readlineInterface.on("line", (line: string) => {
        this.handleLine(line);
      });

      this.readlineInterface.on("close", () => {
        if (!this.isShuttingDown) {
          const error = new Error("Bridge process stdout closed unexpectedly");
          this.notifyError(error);
        }
        this.cleanup();
      });
    }

    // Handle stderr - log for debugging but don't treat as fatal
    if (this.process.stderr) {
      this.process.stderr.on("data", (data: Buffer) => {
        const stderr = data.toString("utf-8").trim();
        if (stderr) {
          // Log to console.error for debugging; could also emit as event
          console.error(`[bridge stderr] ${stderr}`);
        }
      });
    }

    // Handle process exit
    this.process.on("exit", (code: number | null, signal: string | null) => {
      if (!this.isShuttingDown && code !== 0 && code !== null) {
        const error = new Error(
          `Bridge process exited with code ${code}${signal ? ` (signal: ${signal})` : ""}`,
        );
        this.notifyError(error);
      }
      // Reject all pending requests
      for (const [, pending] of this.pending) {
        pending.reject(new Error("Bridge process terminated"));
      }
      this.pending.clear();
      this.cleanup();
    });

    this.process.on("error", (error: Error) => {
      this.notifyError(error);
    });
  }

  public onEvent(listener: (event: BridgeEvent) => void): () => void {
    this.eventListeners.add(listener);
    return () => {
      this.eventListeners.delete(listener);
    };
  }

  public onError(listener: (error: Error) => void): () => void {
    this.errorListeners.add(listener);
    return () => {
      this.errorListeners.delete(listener);
    };
  }

  public request(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
    if (!this.process || !this.process.stdin || this.process.stdin.destroyed) {
      return Promise.reject(new Error("Bridge process is not running. Call start() first."));
    }

    const requestId = String(this.nextId++);
    const payload = {
      id: requestId,
      method,
      params,
    };

    return new Promise((resolve, reject) => {
      this.pending.set(requestId, { resolve, reject });
      try {
        this.process!.stdin!.write(`${JSON.stringify(payload)}\n`);
      } catch (error) {
        const err = error instanceof Error ? error : new Error(String(error));
        this.pending.delete(requestId);
        reject(err);
      }
    });
  }

  public async shutdown(): Promise<void> {
    if (this.isShuttingDown || !this.process) {
      return;
    }

    this.isShuttingDown = true;

    // Try graceful shutdown first
    try {
      await this.request("session.shutdown", {});
    } catch {
      // Ignore shutdown request failures
    }

    // Clean up resources
    this.cleanup();

    // Kill the process if still running
    if (this.process && !this.process.killed) {
      this.process.kill("SIGTERM");
      // Give it a moment to exit gracefully
      await new Promise((resolve) => setTimeout(resolve, 100));
      if (!this.process.killed) {
        this.process.kill("SIGKILL");
      }
    }

    this.process = null;
  }

  // State persistence methods for Ink TUI stateful support
  public async stateGet<T = unknown>(
    namespace: string,
    key: string,
    defaultValue?: T,
  ): Promise<{ value: T | undefined; found: boolean; timestamp?: number; error?: string }> {
    const response = (await this.request("state.get", {
      namespace,
      key,
      default: defaultValue,
    })) as {
      value: T;
      found: boolean;
      timestamp?: number;
      error?: string;
    };
    return response;
  }

  public async stateSet<T = unknown>(
    namespace: string,
    key: string,
    value: T,
  ): Promise<{ ok: boolean; path?: string; error?: string }> {
    const response = (await this.request("state.set", {
      namespace,
      key,
      value,
    })) as {
      ok: boolean;
      path?: string;
      error?: string;
    };
    return response;
  }

  public async stateDelete(
    namespace: string,
    key: string,
  ): Promise<{ ok: boolean; existed: boolean; error?: string }> {
    const response = (await this.request("state.delete", {
      namespace,
      key,
    })) as {
      ok: boolean;
      existed: boolean;
      error?: string;
    };
    return response;
  }

  public async stateList(
    namespace: string,
  ): Promise<{ keys: Array<{ key: string; timestamp?: number }>; count: number }> {
    const response = (await this.request("state.list", {
      namespace,
    })) as {
      keys: Array<{ key: string; timestamp?: number }>;
      count: number;
    };
    return response;
  }

  public async stateClear(namespace: string): Promise<{ ok: boolean; deletedCount: number }> {
    const response = (await this.request("state.clear", {
      namespace,
    })) as {
      ok: boolean;
      deleted_count: number;
    };
    return { ok: response.ok, deletedCount: response.deleted_count };
  }

  // Modal Volume and Sandbox methods
  public async volumeRead(
    volumeName: string,
    path: string,
    encoding = "utf-8",
  ): Promise<{ content: string; path: string; volume_name: string; encoding: string }> {
    return this.request("volume.read", { volume_name: volumeName, path, encoding }) as Promise<{
      content: string;
      path: string;
      volume_name: string;
      encoding: string;
    }>;
  }

  public async volumeWrite(
    volumeName: string,
    path: string,
    content: string,
    encoding = "utf-8",
  ): Promise<{ ok: boolean; path: string; volume_name: string; bytes_written: number }> {
    return this.request("volume.write", {
      volume_name: volumeName,
      path,
      content,
      encoding,
    }) as Promise<{ ok: boolean; path: string; volume_name: string; bytes_written: number }>;
  }

  public async volumeList(
    volumeName: string,
    prefix = "",
    recursive = false,
  ): Promise<{
    files: Array<{ path: string; type: string; size?: number; mtime?: number }>;
    count: number;
    volume_name: string;
  }> {
    return this.request("volume.list", { volume_name: volumeName, prefix, recursive }) as Promise<{
      files: Array<{ path: string; type: string; size?: number; mtime?: number }>;
      count: number;
      volume_name: string;
    }>;
  }

  public async volumeDelete(
    volumeName: string,
    path: string,
    recursive = false,
  ): Promise<{ ok: boolean; path: string; volume_name: string }> {
    return this.request("volume.delete", { volume_name: volumeName, path, recursive }) as Promise<{
      ok: boolean;
      path: string;
      volume_name: string;
    }>;
  }

  public async volumeInfo(volumeName: string): Promise<{
    name: string;
    version: number;
    exists: boolean;
    file_count: number;
    dir_count: number;
  }> {
    return this.request("volume.info", { volume_name: volumeName }) as Promise<{
      name: string;
      version: number;
      exists: boolean;
      file_count: number;
      dir_count: number;
    }>;
  }

  public async memoryRead<T = unknown>(
    key: string,
    volumeName?: string,
  ): Promise<{ content: T; path: string; volume_name: string; encoding: string }> {
    return this.request("memory.read", { key, volume_name: volumeName }) as Promise<{
      content: T;
      path: string;
      volume_name: string;
      encoding: string;
    }>;
  }

  public async memoryWrite<T = unknown>(
    key: string,
    data: T,
    volumeName?: string,
  ): Promise<{
    ok: boolean;
    path: string;
    volume_name: string;
    bytes_written: number;
    key: string;
  }> {
    return this.request("memory.write", { key, data, volume_name: volumeName }) as Promise<{
      ok: boolean;
      path: string;
      volume_name: string;
      bytes_written: number;
      key: string;
    }>;
  }

  public async memoryList(
    volumeName?: string,
    prefix?: string,
  ): Promise<{
    keys: Array<{ key: string; mtime?: number; size?: number }>;
    count: number;
    volume_name: string;
  }> {
    return this.request("memory.list", { volume_name: volumeName, prefix }) as Promise<{
      keys: Array<{ key: string; mtime?: number; size?: number }>;
      count: number;
      volume_name: string;
    }>;
  }

  public async sandboxList(
    appName?: string,
    tag?: string,
  ): Promise<{
    sandboxes: Array<{
      id: string;
      app_name: string;
      tags: string[];
      status: string;
      created_at?: string;
    }>;
    count: number;
  }> {
    return this.request("sandbox.list", { app_name: appName, tag }) as Promise<{
      sandboxes: Array<{
        id: string;
        app_name: string;
        tags: string[];
        status: string;
        created_at?: string;
      }>;
      count: number;
    }>;
  }

  public async sandboxExec(
    command: string,
    options?: { image?: string; volume_name?: string; timeout?: number; app_name?: string },
  ): Promise<{
    ok: boolean;
    returncode: number;
    stdout: string;
    stderr: string;
    sandbox_id: string;
  }> {
    return this.request("sandbox.exec", { command, ...options }) as Promise<{
      ok: boolean;
      returncode: number;
      stdout: string;
      stderr: string;
      sandbox_id: string;
    }>;
  }

  private handleLine(line: string): void {
    if (!line.trim()) {
      return;
    }

    let payload: Record<string, unknown>;
    try {
      const parsedPayload = BridgeUnknownRecordSchema.safeParse(JSON.parse(line));
      if (!parsedPayload.success) {
        return;
      }
      payload = parsedPayload.data;
    } catch {
      // Invalid JSON, ignore
      return;
    }

    // Check if this is a response (has 'id' field)
    const responsePayload = BridgeResponseEnvelopeSchema.safeParse(payload);
    if (responsePayload.success) {
      const { id, error, result } = responsePayload.data;
      const pending = this.pending.get(id);
      if (!pending) {
        // No pending request for this id, could be a late response
        return;
      }
      this.pending.delete(id);

      if (error) {
        const parsedError = BridgeRpcErrorSchema.safeParse(error);
        const message = String(
          parsedError.success ? (parsedError.data.message ?? "Bridge error") : "Bridge error",
        );
        const code = String(parsedError.success ? (parsedError.data.code ?? "UNKNOWN") : "UNKNOWN");
        pending.reject(new Error(`[${code}] ${message}`));
      } else {
        pending.resolve(result);
      }
      return;
    }

    // Check if this is an event (has 'event' field)
    const eventPayload = BridgeEventEnvelopeSchema.safeParse(payload);
    if (eventPayload.success) {
      const event: BridgeEvent = {
        event: eventPayload.data.event,
        params: eventPayload.data.params,
      };
      for (const listener of this.eventListeners) {
        try {
          listener(event);
        } catch (_err) {
          // Ignore errors from event listeners
        }
      }
      return;
    }

    // Legacy event format: {type: "event", data: {...}}
    const legacyEventPayload = BridgeLegacyEventEnvelopeSchema.safeParse(payload);
    if (legacyEventPayload.success) {
      const eventData = legacyEventPayload.data.data;
      const event: BridgeEvent = {
        event: typeof eventData.kind === "string" ? eventData.kind : "unknown",
        params: eventData,
      };
      for (const listener of this.eventListeners) {
        try {
          listener(event);
        } catch (_err) {
          // Ignore errors from event listeners
        }
      }
    }
  }

  private notifyError(error: Error): void {
    for (const listener of this.errorListeners) {
      try {
        listener(error);
      } catch {
        // Ignore errors from error listeners
      }
    }
  }

  private cleanup(): void {
    if (this.readlineInterface) {
      this.readlineInterface.close();
      this.readlineInterface = null;
    }

    // Close stdin to signal EOF to Python process
    if (this.process?.stdin && !this.process.stdin.destroyed) {
      this.process.stdin.end();
    }
  }
}

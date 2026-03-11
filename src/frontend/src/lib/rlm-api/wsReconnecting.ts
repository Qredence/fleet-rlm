import { parseWsServerFrame, createWsError } from "@/lib/rlm-api/wsFrameParser";
import { getAccessToken } from "@/lib/auth/tokenStore";
import type {
  WsClientMessage,
  StreamWsOptions,
  WsConnectionStatus,
  WsEventKind,
  WsCancelRequest,
} from "@/lib/rlm-api/wsTypes";

let backendSessionFallbackSequence = 0;

interface RetryState {
  attempt: number;
  backoffTimer: ReturnType<typeof setTimeout> | null;
  aborted: boolean;
}

const DEFAULT_MAX_RETRIES = 5;
const DEFAULT_INITIAL_BACKOFF = 1000;
const DEFAULT_MAX_BACKOFF = 30000;
const DEFAULT_FIRST_FRAME_TIMEOUT = 15000;

function calculateBackoff(
  attempt: number,
  initialBackoff: number,
  maxBackoff: number,
): number {
  const backoff = initialBackoff * Math.pow(2, attempt);
  return Math.min(backoff, maxBackoff);
}

async function sleep(ms: number, signal?: AbortSignal): Promise<boolean> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(true), ms);
    const onAbort = () => {
      clearTimeout(timer);
      resolve(false);
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export function createBackendSessionId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  backendSessionFallbackSequence += 1;
  return `fleet-${Date.now()}-${backendSessionFallbackSequence}`;
}

export async function createReconnectingWs(
  message: WsClientMessage | null,
  options: StreamWsOptions & {
    url: string;
    terminalEventKinds?: WsEventKind[];
    abortMode?: "cancel" | "close";
    abortTimeoutMs?: number;
  },
): Promise<void> {
  const {
    signal,
    onFrame,
    onStatusChange,
    maxRetries = DEFAULT_MAX_RETRIES,
    initialBackoff = DEFAULT_INITIAL_BACKOFF,
    maxBackoff = DEFAULT_MAX_BACKOFF,
    firstFrameTimeoutMs = DEFAULT_FIRST_FRAME_TIMEOUT,
    terminalEventKinds = ["final", "cancelled"],
    abortMode = "close",
    abortTimeoutMs = 1500,
  } = options;

  const retryState: RetryState = {
    attempt: 0,
    backoffTimer: null,
    aborted: false,
  };

  const updateStatus = (status: WsConnectionStatus) => {
    onStatusChange?.(status);
  };

  const cleanup = () => {
    if (retryState.backoffTimer) {
      clearTimeout(retryState.backoffTimer);
      retryState.backoffTimer = null;
    }
  };

  const attemptConnection = async (): Promise<void> => {
    return new Promise<void>((resolve, reject) => {
      let settled = false;
      let completed = false;
      let abortTimer: ReturnType<typeof setTimeout> | null = null;
      let firstFrameTimer: ReturnType<typeof setTimeout> | null = null;
      let firstFrameSeen = false;

      updateStatus(retryState.attempt > 0 ? "reconnecting" : "connecting");

      const wsUrlObj = new URL(options.url);
      const token = getAccessToken();
      if (token) {
        wsUrlObj.searchParams.set("access_token", token);
      }
      const socket = new WebSocket(wsUrlObj.toString());

      const finish = (fn: () => void) => {
        if (settled) return;
        settled = true;
        if (abortTimer) {
          clearTimeout(abortTimer);
          abortTimer = null;
        }
        if (firstFrameTimer) {
          clearTimeout(firstFrameTimer);
          firstFrameTimer = null;
        }
        fn();
      };

      const safeClose = () => {
        if (
          socket.readyState === WebSocket.OPEN ||
          socket.readyState === WebSocket.CONNECTING
        ) {
          socket.close();
        }
      };

      const abortHandler = () => {
        retryState.aborted = true;
        cleanup();

        if (abortMode === "cancel" && socket.readyState === WebSocket.OPEN) {
          const cancel: WsCancelRequest = { type: "cancel" };
          socket.send(JSON.stringify(cancel));
          abortTimer = setTimeout(() => {
            console.warn("WebSocket: Abort timeout reached. Forcibly closing connection.");
            safeClose();
            updateStatus("disconnected");
            finish(resolve);
          }, abortTimeoutMs);
          return;
        }

        safeClose();
        updateStatus("disconnected");
        finish(resolve);
      };

      if (signal) {
        if (signal.aborted) {
          abortHandler();
          return;
        }
        signal.addEventListener("abort", abortHandler, { once: true });
      }

      socket.addEventListener("open", () => {
        updateStatus("connected");
        if (message) {
          socket.send(JSON.stringify(message));
          if (firstFrameTimeoutMs > 0) {
            firstFrameTimer = setTimeout(() => {
              if (settled || completed || retryState.aborted || firstFrameSeen) {
                return;
              }
              completed = true;
              safeClose();
              updateStatus("disconnected");
              finish(() =>
                reject(
                  createWsError(
                    `No response arrived from the server within ${Math.ceil(firstFrameTimeoutMs / 1000)} seconds. Try again or check the backend logs.`,
                  ),
                ),
              );
            }, firstFrameTimeoutMs);
          }
        }
      });

      socket.addEventListener("message", (event) => {
        try {
          const parsed = JSON.parse(String(event.data)) as Record<
            string,
            unknown
          >;
          const frame = parseWsServerFrame(parsed);
          if (!frame) return;

          firstFrameSeen = true;
          if (firstFrameTimer) {
            clearTimeout(firstFrameTimer);
            firstFrameTimer = null;
          }

          onFrame(frame);

          if (frame.type === "error") {
            completed = true;
            safeClose();
            updateStatus("disconnected");
            finish(() => reject(createWsError(frame.message)));
            return;
          }

          const kind = frame.data.kind;
          if (terminalEventKinds.includes(kind)) {
            completed = true;
            safeClose();
            updateStatus("disconnected");
            finish(resolve);
          } else if (kind === "error") {
            completed = true;
            safeClose();
            updateStatus("disconnected");
            finish(() =>
              reject(createWsError(frame.data.text || "Server stream error")),
            );
          }
        } catch {
          // Ignore malformed frames to avoid taking down the stream.
        }
      });

      socket.addEventListener("error", () => {
        safeClose();
        // Don't reject here - let the close handler deal with reconnection
      });

      socket.addEventListener("close", async (event) => {
        if (settled) return;

        if (completed || retryState.aborted || event.code === 1000) {
          updateStatus("disconnected");
          finish(resolve);
          return;
        }

        if (retryState.attempt >= maxRetries) {
          updateStatus("disconnected");
          finish(() =>
            reject(
              createWsError(
                `WebSocket connection failed after ${maxRetries} retries`,
              ),
            ),
          );
          return;
        }

        retryState.attempt += 1;
        const backoffMs = calculateBackoff(
          retryState.attempt - 1,
          initialBackoff,
          maxBackoff,
        );

        const shouldContinue = await sleep(backoffMs, signal);
        if (!shouldContinue || retryState.aborted) {
          updateStatus("disconnected");
          finish(resolve);
          return;
        }

        try {
          await attemptConnection();
          finish(resolve);
        } catch (err) {
          finish(() => reject(err));
        }
      });
    });
  };

  try {
    await attemptConnection();
  } finally {
    cleanup();
  }
}

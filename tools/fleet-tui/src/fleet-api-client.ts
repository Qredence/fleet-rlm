import type { components } from "./generated/openapi.js";

export type FleetIdentity = {
  token?: string;
  userId?: string;
  workspaceId?: string;
};

export type FleetSession = components["schemas"]["SessionDetailResponse"];

export type FleetTurnPart = {
  type: string;
  text?: string;
  state?: string;
  toolName?: string;
  toolCallId?: string;
  input?: unknown;
  output?: unknown;
  errorText?: string;
  data?: unknown;
};

type GeneratedUIMessage = components["schemas"]["UIMessageResponse"];
export type FleetTurn = Omit<GeneratedUIMessage, "parts"> & {
  parts: FleetTurnPart[];
};

type FleetTurnPage = Omit<components["schemas"]["SessionTurnPageResponse"], "items"> & {
  items: FleetTurn[];
};

export class FleetApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "FleetApiError";
    this.status = status;
  }
}

export class FleetApiClient {
  readonly baseUrl: string;
  readonly identity: FleetIdentity;

  constructor({ baseUrl, identity = {} }: { baseUrl: string; identity?: FleetIdentity }) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.identity = identity;
  }

  async createSession(): Promise<FleetSession> {
    return this.requestJson<FleetSession>("/api/sessions", {
      method: "POST",
      body: JSON.stringify({}),
    });
  }

  async getSession(sessionId: string): Promise<FleetSession> {
    return this.requestJson<FleetSession>(`/api/sessions/${encodeURIComponent(sessionId)}`);
  }

  async listTurns(sessionId: string): Promise<FleetTurn[]> {
    const turns: FleetTurn[] = [];
    let afterSequence: number | null = null;
    for (let pageNumber = 0; pageNumber < Number.MAX_SAFE_INTEGER; pageNumber += 1) {
      const cursor: string = afterSequence === null ? "" : `&after_sequence=${afterSequence}`;
      const page: FleetTurnPage = await this.requestJson<FleetTurnPage>(
        `/api/sessions/${encodeURIComponent(sessionId)}/turns?limit=200${cursor}`,
      );
      turns.push(...page.items);
      if (page.next_after_sequence == null) {
        return turns;
      }
      afterSequence = page.next_after_sequence ?? null;
    }
    throw new FleetApiError(502, "Fleet API returned too many Turn pages");
  }

  async streamTurn({
    message,
    sessionId,
    idempotencyKey,
    signal,
  }: {
    message: string;
    sessionId: string;
    idempotencyKey: string;
    signal?: AbortSignal;
  }): Promise<Response> {
    const response = await this.fetch(
      `${this.baseUrl}/api/sessions/${encodeURIComponent(sessionId)}/turns`,
      {
        method: "POST",
        headers: {
          ...this.headers(),
          "content-type": "application/json",
          "idempotency-key": idempotencyKey,
        },
        body: JSON.stringify({ text: message, attachment_ids: [] }),
        signal,
      },
    );

    if (!response.ok) {
      throw await this.toApiError(response);
    }
    if (response.headers.get("x-vercel-ai-ui-message-stream") !== "v1") {
      throw new FleetApiError(502, "Fleet API did not return an AI SDK UI v1 stream");
    }
    if (!response.body) {
      throw new FleetApiError(502, "Fleet API returned an empty SSE response");
    }
    return response;
  }

  async requestCancellation(runId: string): Promise<components["schemas"]["CancellationResponse"]> {
    return this.requestJson(`/api/runs/${encodeURIComponent(runId)}/cancellation`, {
      method: "PUT",
    });
  }

  async downloadArtifact(artifactId: string): Promise<Response> {
    const response = await this.fetch(
      `${this.baseUrl}/api/artifacts/${encodeURIComponent(artifactId)}/content`,
      { method: "GET", headers: this.headers() },
    );
    if (!response.ok) {
      throw await this.toApiError(response);
    }
    return response;
  }

  private async requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await this.fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
        ...this.headers(),
        ...(init.body ? { "content-type": "application/json" } : {}),
        ...(init.headers ?? {}),
      },
    });
    if (!response.ok) {
      throw await this.toApiError(response);
    }
    return (await response.json()) as T;
  }

  private headers(): HeadersInit {
    if (this.identity.token) {
      return { authorization: `Bearer ${this.identity.token}` };
    }
    return {
      ...(this.identity.userId ? { "x-fleet-user-id": this.identity.userId } : {}),
      ...(this.identity.workspaceId ? { "x-fleet-workspace-id": this.identity.workspaceId } : {}),
    };
  }

  private async fetch(url: string, init: RequestInit): Promise<Response> {
    try {
      return await fetch(url, init);
    } catch (error) {
      const detail = error instanceof Error && error.message ? ` (${error.message})` : "";
      throw new FleetApiError(
        0,
        `Cannot connect to Fleet API at ${this.baseUrl}${detail}. Start it with: uv run fleet-rlm serve-api --port 8000`,
      );
    }
  }

  private async toApiError(response: Response): Promise<FleetApiError> {
    const body = await response.text();
    let message = `Fleet API request failed (${response.status})`;
    try {
      const parsed = JSON.parse(body) as { message?: unknown; detail?: unknown };
      if (typeof parsed.message === "string" && parsed.message.trim()) {
        message = parsed.message;
      }
      if (typeof parsed.detail === "string" && parsed.detail.trim()) {
        message = parsed.detail;
      }
    } catch {
      // The public API may return an empty or non-JSON error body.
    }
    return new FleetApiError(response.status, message);
  }
}

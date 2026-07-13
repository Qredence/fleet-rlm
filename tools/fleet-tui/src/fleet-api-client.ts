export type FleetIdentity = {
  userId?: string;
  workspaceId?: string;
};

export type FleetSession = {
  id: string;
  title: string;
};

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

export type FleetTurn = {
  id: string;
  sequence: number;
  role: string;
  content: string;
  status: string;
  run_id?: string | null;
  parts: FleetTurnPart[];
  metadata?: Record<string, unknown> | null;
};

type FleetTurnPage = {
  items: FleetTurn[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
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
    let offset = 0;
    do {
      const page = await this.requestJson<FleetTurnPage>(
        `/api/sessions/${encodeURIComponent(sessionId)}/turns?limit=200&offset=${offset}`,
      );
      turns.push(...page.items);
      if (!page.has_more) {
        return turns;
      }
      offset += page.items.length;
    } while (true);
  }

  async streamChat({
    message,
    sessionId,
    signal,
  }: {
    message: string;
    sessionId: string;
    signal?: AbortSignal;
  }): Promise<Response> {
    const response = await this.fetch(`${this.baseUrl}/api/chat`, {
      method: "POST",
      headers: {
        ...this.headers(),
        "content-type": "application/json",
        "idempotency-key": crypto.randomUUID(),
      },
      body: JSON.stringify({ message, session_id: sessionId }),
      signal,
    });

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
    return {
      ...(this.identity.userId ? { "x-fleet-user-id": this.identity.userId } : {}),
      ...(this.identity.workspaceId
        ? { "x-fleet-workspace-id": this.identity.workspaceId }
        : {}),
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
      const parsed = JSON.parse(body) as { detail?: unknown };
      if (typeof parsed.detail === "string" && parsed.detail.trim()) {
        message = parsed.detail;
      }
    } catch {
      // The public API may return an empty or non-JSON error body.
    }
    return new FleetApiError(response.status, message);
  }
}

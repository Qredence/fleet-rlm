import type { components } from "./generated/openapi.js";
import { record } from "./tui/coerce.js";

export type FleetSession = components["schemas"]["SessionDetailResponse"];
export type FleetTurn = components["schemas"]["UIMessageResponse"];
export type FleetTurnPart = FleetTurn["parts"][number];
type FleetTurnPage = components["schemas"]["SessionTurnPageResponse"];
export type FleetSkillCard = components["schemas"]["SkillCardResponse"];
export type FleetSettingsPolicy = components["schemas"]["SettingsPolicyResponse"];
export type FleetSettingsPatch = components["schemas"]["SettingsPolicyPatchRequest"];
export type FleetVolumeTree = components["schemas"]["VolumeTreeResponse"];
export type FleetAttachment = components["schemas"]["AttachmentResponse"];
export type FleetWorkspaceFileList = components["schemas"]["WorkspaceFileListResponse"];
export type FleetWorkspaceFileEntry = components["schemas"]["WorkspaceFileEntryResponse"];
export type FleetWorkspaceFileRead = components["schemas"]["WorkspaceFileReadResponse"];

export type FleetSkillSelection = {
  id: string;
  expected_version: string;
};

export type SessionListPage = components["schemas"]["SessionListResponse"];
type SessionPatch = components["schemas"]["SessionPatchRequest"];

export class FleetApiError extends Error {
  readonly status: number;
  readonly correlationId?: string;
  readonly code?: string;

  constructor(status: number, message: string, correlationId?: string, code?: string) {
    super(message);
    this.name = "FleetApiError";
    this.status = status;
    this.correlationId = correlationId;
    this.code = code;
  }
}

export class FleetApiClient {
  readonly baseUrl: string;
  constructor({ baseUrl }: { baseUrl: string }) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
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

  async updateSession(sessionId: string, patch: SessionPatch): Promise<FleetSession> {
    return this.requestJson<FleetSession>(`/api/sessions/${encodeURIComponent(sessionId)}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
  }

  async listSessions(
    params: {
      limit?: number;
      offset?: number;
      search?: string;
      status?: "active" | "archived";
    } = {},
  ): Promise<SessionListPage> {
    const search = new URLSearchParams();
    if (params.limit !== undefined) search.set("limit", String(params.limit));
    if (params.offset !== undefined) search.set("offset", String(params.offset));
    if (params.search) search.set("search", params.search);
    if (params.status) search.set("status", params.status);
    const suffix = search.toString();
    return this.requestJson<SessionListPage>(`/api/sessions${suffix ? `?${suffix}` : ""}`);
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

  async listSkills(): Promise<FleetSkillCard[]> {
    return this.requestJson<FleetSkillCard[]>("/api/skills");
  }

  async getSettings(): Promise<FleetSettingsPolicy> {
    return this.requestJson<FleetSettingsPolicy>("/api/settings");
  }

  async updateSettings(patch: FleetSettingsPatch): Promise<FleetSettingsPolicy> {
    return this.requestJson<FleetSettingsPolicy>("/api/settings", {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
  }

  async setProfile(name: string, revision: string): Promise<FleetSettingsPolicy> {
    return this.requestJson<FleetSettingsPolicy>("/api/settings", {
      method: "PATCH",
      body: JSON.stringify({ profile: name, revision }),
    });
  }

  async listVolumeTree(
    params: { root?: string; maxDepth?: number; maxFiles?: number } = {},
  ): Promise<FleetVolumeTree> {
    const query = new URLSearchParams();
    if (params.root) query.set("root", params.root);
    if (params.maxDepth !== undefined) query.set("max_depth", String(params.maxDepth));
    if (params.maxFiles !== undefined) query.set("max_files", String(params.maxFiles));
    const suffix = query.toString();
    return this.requestJson<FleetVolumeTree>(`/api/volume/tree${suffix ? `?${suffix}` : ""}`);
  }

  /** Upload one Attachment through the lifecycle-owned multipart endpoint. */
  async uploadAttachment(file: {
    name: string;
    bytes: Uint8Array;
    contentType?: string;
  }): Promise<FleetAttachment> {
    const form = new FormData();
    form.append(
      "attachment",
      new Blob([file.bytes as unknown as BlobPart], {
        type: file.contentType ?? "application/octet-stream",
      }),
      file.name,
    );
    const response = await this.fetch(`${this.baseUrl}/api/attachments`, {
      method: "POST",
      body: form,
    });
    if (!response.ok) throw await this.toApiError(response);
    return (await response.json()) as FleetAttachment;
  }

  /** List Workspace files/ root entries under a relative path via /api/files. */
  async listWorkspaceFiles(
    params: { path?: string; limit?: number; after?: string } = {},
  ): Promise<FleetWorkspaceFileList> {
    const query = new URLSearchParams();
    if (params.path) query.set("path", params.path);
    if (params.limit !== undefined) query.set("limit", String(params.limit));
    if (params.after) query.set("after", params.after);
    const suffix = query.toString();
    return this.requestJson<FleetWorkspaceFileList>(`/api/files${suffix ? `?${suffix}` : ""}`);
  }

  /** Stat one Workspace files/ root entry. */
  async statWorkspaceFile(path: string): Promise<FleetWorkspaceFileEntry> {
    return this.requestJson<FleetWorkspaceFileEntry>(
      `/api/files/stat?path=${encodeURIComponent(path)}`,
    );
  }

  /** Read one Workspace files/ root text page (server caps max_chars at 10 000). */
  async readWorkspaceFile(
    path: string,
    maxChars?: number,
    cursor?: string,
  ): Promise<FleetWorkspaceFileRead> {
    const query = new URLSearchParams();
    query.set("path", path);
    if (maxChars !== undefined) query.set("max_chars", String(maxChars));
    if (cursor) query.set("cursor", cursor);
    return this.requestJson<FleetWorkspaceFileRead>(`/api/files/content?${query.toString()}`);
  }

  async streamTurn({
    message,
    sessionId,
    idempotencyKey,
    skillSelections = [],
    attachmentIds = [],
    onStreamOpen,
    signal,
  }: {
    message: string;
    sessionId: string;
    idempotencyKey: string;
    skillSelections?: readonly FleetSkillSelection[];
    attachmentIds?: readonly string[];
    onStreamOpen?: () => void;
    signal?: AbortSignal;
  }): Promise<Response> {
    const response = await this.fetch(
      `${this.baseUrl}/api/sessions/${encodeURIComponent(sessionId)}/turns`,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "idempotency-key": idempotencyKey,
        },
        body: JSON.stringify({
          text: message,
          attachment_ids: attachmentIds,
          skill_selections: skillSelections,
        }),
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
    onStreamOpen?.();
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
      { method: "GET" },
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
        ...(init.body ? { "content-type": "application/json" } : {}),
        ...(init.headers ?? {}),
      },
    });
    if (!response.ok) {
      throw await this.toApiError(response);
    }
    return (await response.json()) as T;
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
    let code: string | undefined;
    try {
      const parsed = JSON.parse(body) as { code?: unknown; message?: unknown; detail?: unknown };
      const detail = record(parsed.detail);
      message =
        nonEmptyString(detail.message) ??
        nonEmptyString(parsed.message) ??
        nonEmptyString(parsed.detail) ??
        message;
      code = nonEmptyString(detail.code) ?? nonEmptyString(parsed.code);
    } catch {
      // The public API may return an empty or non-JSON error body.
    }
    return new FleetApiError(
      response.status,
      message,
      response.headers.get("x-request-id") ?? undefined,
      code,
    );
  }
}

function nonEmptyString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

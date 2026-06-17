import { rlmApiClient } from "@/lib/rlm-api/client";
import { withQuery } from "@/lib/rlm-api/query";
import type { components } from "@/lib/rlm-api/generated/openapi";

export type VolumeProvider = NonNullable<components["schemas"]["VolumeTreeResponse"]["provider"]>;
export type VolumeTreeNode = components["schemas"]["VolumeTreeNode"];
export type VolumeTreeResponse = components["schemas"]["VolumeTreeResponse"];
export type VolumeFileContentResponse = components["schemas"]["VolumeFileContentResponse"];

const BASE = "/api/v1/runtime/volume";
const VOLUME_REQUEST_TIMEOUT_MS = 120_000;

export interface VolumeTreeInput {
  provider: VolumeProvider;
  maxDepth?: number;
  maxEntries?: number;
  rootPath?: string;
}

export interface VolumeFileInput {
  provider: VolumeProvider;
  path: string;
  maxBytes?: number;
}

export const volumesEndpoints = {
  tree(input: VolumeTreeInput, signal?: AbortSignal) {
    return rlmApiClient.get<VolumeTreeResponse>(
      withQuery(`${BASE}/tree`, {
        provider: input.provider,
        max_depth: input.maxDepth,
        max_entries: input.maxEntries,
        root_path: input.rootPath,
      }),
      signal,
      VOLUME_REQUEST_TIMEOUT_MS,
    );
  },

  file(input: VolumeFileInput, signal?: AbortSignal) {
    return rlmApiClient.get<VolumeFileContentResponse>(
      withQuery(`${BASE}/file`, {
        provider: input.provider,
        path: input.path,
        max_bytes: input.maxBytes,
      }),
      signal,
      VOLUME_REQUEST_TIMEOUT_MS,
    );
  },
};

import { typedClient, unwrap, withTimeout } from "@/lib/rlm-api/typed-client";
import type { components } from "@/lib/rlm-api/generated/openapi";

export type VolumeProvider = NonNullable<components["schemas"]["VolumeTreeResponse"]["provider"]>;
export type VolumeTreeNode = components["schemas"]["VolumeTreeNode"];
export type VolumeTreeResponse = components["schemas"]["VolumeTreeResponse"];
export type VolumeFileContentResponse = components["schemas"]["VolumeFileContentResponse"];

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
    return unwrap(
      typedClient.GET("/api/v1/runtime/volume/tree", {
        params: {
          query: {
            provider: input.provider,
            max_depth: input.maxDepth,
            max_entries: input.maxEntries,
            root_path: input.rootPath,
          },
        },
        signal: withTimeout(signal, VOLUME_REQUEST_TIMEOUT_MS),
      }),
    );
  },

  file(input: VolumeFileInput, signal?: AbortSignal) {
    return unwrap(
      typedClient.GET("/api/v1/runtime/volume/file", {
        params: {
          query: {
            provider: input.provider,
            path: input.path,
            max_bytes: input.maxBytes,
          },
        },
        signal: withTimeout(signal, VOLUME_REQUEST_TIMEOUT_MS),
      }),
    );
  },
};

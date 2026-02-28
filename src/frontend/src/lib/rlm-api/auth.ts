import { rlmApiClient } from "@/lib/rlm-api/client";
import type {
  AuthLoginResponse,
  AuthLogoutResponse,
  AuthMeResponse,
} from "@/lib/rlm-api/types";

export const authEndpoints = {
  login(signal?: AbortSignal) {
    return rlmApiClient.post<AuthLoginResponse>(
      "/api/v1/auth/login",
      undefined,
      signal,
    );
  },

  logout(signal?: AbortSignal) {
    return rlmApiClient.post<AuthLogoutResponse>(
      "/api/v1/auth/logout",
      undefined,
      signal,
    );
  },

  me(signal?: AbortSignal) {
    return rlmApiClient.get<AuthMeResponse>("/api/v1/auth/me", signal);
  },
};

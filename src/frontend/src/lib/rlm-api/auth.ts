import { clearAccessToken, setAccessToken } from "@/lib/auth/tokenStore";
import { rlmApiClient } from "@/lib/rlm-api/client";
import type {
  AuthLoginResponse,
  AuthLogoutResponse,
  AuthMeResponse,
} from "@/lib/rlm-api/types";

export const authEndpoints = {
  async login(signal?: AbortSignal) {
    const response = await rlmApiClient.post<AuthLoginResponse>(
      "/api/v1/auth/login",
      undefined,
      signal,
    );
    setAccessToken(response.token);
    return response;
  },

  async logout(signal?: AbortSignal) {
    try {
      return await rlmApiClient.post<AuthLogoutResponse>(
        "/api/v1/auth/logout",
        undefined,
        signal,
      );
    } finally {
      clearAccessToken();
    }
  },

  me(signal?: AbortSignal) {
    return rlmApiClient.get<AuthMeResponse>("/api/v1/auth/me", signal);
  },

  clearLocalAuth() {
    clearAccessToken();
  },

  setToken(token: string | null) {
    setAccessToken(token);
  },
};

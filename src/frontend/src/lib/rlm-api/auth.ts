import { clearAccessToken, setAccessToken } from "@/lib/auth/tokenStore";
import { rlmApiClient } from "@/lib/rlm-api/client";
import type { AuthMeResponse } from "@/lib/rlm-api/types";

export const authEndpoints = {
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

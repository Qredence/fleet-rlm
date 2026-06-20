import { clearAccessToken, setAccessToken } from "@/lib/auth/token-store";
import { typedClient, unwrap, withTimeout } from "@/lib/rlm-api/typed-client";

export const authEndpoints = {
  me(signal?: AbortSignal) {
    return unwrap(
      typedClient.GET("/api/v1/auth/me", { signal: withTimeout(signal) }),
    );
  },

  createWsTicket(signal?: AbortSignal) {
    return unwrap(
      typedClient.POST("/api/v1/auth/ws-ticket", { signal: withTimeout(signal) }),
    );
  },

  clearLocalAuth() {
    clearAccessToken();
  },

  setToken(token: string | null) {
    setAccessToken(token);
  },
};

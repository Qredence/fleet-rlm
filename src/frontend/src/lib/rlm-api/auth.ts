import { clearAccessToken, setAccessToken } from "@/lib/auth/token-store";
import { rlmApiClient } from "@/lib/rlm-api/client";
import type { AuthMeResponse, WebSocketTicketResponse } from "@/lib/rlm-api/types";

export const authEndpoints = {
  me(signal?: AbortSignal) {
    return rlmApiClient.get<AuthMeResponse>("/api/v1/auth/me", signal);
  },

  createWsTicket(signal?: AbortSignal) {
    return rlmApiClient.post<WebSocketTicketResponse>("/api/v1/auth/ws-ticket", undefined, signal);
  },

  clearLocalAuth() {
    clearAccessToken();
  },

  setToken(token: string | null) {
    setAccessToken(token);
  },
};

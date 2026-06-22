import { createAuthClient } from "@neondatabase/neon-js/auth";
import { BetterAuthReactAdapter } from "@neondatabase/neon-js/auth/react";

import { clearAccessToken, setAccessToken } from "@/lib/auth/token-store";
import { trimOrEmpty } from "@/lib/utils/env";

const neonAuthUrl = trimOrEmpty(
  import.meta.env.VITE_NEON_AUTH_URL ?? import.meta.env.NEON_AUTH_URL,
);

export const neonAuthConfig = {
  neonAuthUrl,
} as const;

export function isNeonAuthConfigured(): boolean {
  return !!neonAuthConfig.neonAuthUrl;
}

export const neonAuthClient = isNeonAuthConfigured()
  ? createAuthClient(neonAuthConfig.neonAuthUrl, { adapter: BetterAuthReactAdapter() })
  : null;

type NeonTokenResponse = {
  data?: {
    token?: string;
    session?: {
      token?: string;
    };
  } | null;
  error?: unknown;
};

type NeonTokenClient = {
  token: () => Promise<NeonTokenResponse>;
};

export async function initializeNeonSession(): Promise<string | null> {
  if (!isNeonAuthConfigured() || !neonAuthClient) {
    return null;
  }

  try {
    const session = await neonAuthClient.getSession();
    if (!session || !session.data) {
      clearAccessToken();
      return null;
    }

    const { data, error } = await (neonAuthClient as unknown as NeonTokenClient).token();
    const token = data?.token ?? data?.session?.token;
    if (error || !token) {
      clearAccessToken();
      return null;
    }

    setAccessToken(token);
    return token;
  } catch {
    clearAccessToken();
    return null;
  }
}

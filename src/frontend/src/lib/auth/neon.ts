import { createAuthClient } from "@neondatabase/neon-js/auth";
import { BetterAuthReactAdapter } from "@neondatabase/neon-js/auth/react";

import { clearAccessToken, setAccessToken } from "@/lib/auth/token-store";

/**
 * Neon Auth URL for the fleet-rlm project (eu-central-1).
 * Hardcoded so anyone cloning the repo can authenticate without .env setup.
 * Project: old-bird-44339002 / branch: br-flat-boat-al3qj3hh (main)
 */
const NEON_AUTH_URL =
  "https://ep-broad-water-al4k5bh7.neonauth.c-3.eu-central-1.aws.neon.tech/neondb/auth";

export const neonAuthConfig = {
  neonAuthUrl: NEON_AUTH_URL,
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

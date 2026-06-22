import { useCallback, useEffect, useState, type ReactNode } from "react";

import { AuthContext } from "@/lib/auth/auth-context";
import { MOCK_USER } from "@/lib/auth/auth-mock-user";
import { getAccessToken } from "@/lib/auth/token-store";
import { initializeNeonSession, isNeonAuthConfigured, neonAuthClient } from "@/lib/auth/neon";
import { authEndpoints } from "@/lib/rlm-api/auth";
import type { AuthContextValue, PlanTier, UserProfile } from "@/lib/auth/types";
import { queryClient } from "@/lib/query-client";

interface AuthProviderProps {
  children: ReactNode;
}

function mapProfile(me: Awaited<ReturnType<typeof authEndpoints.me>>): UserProfile {
  return {
    id: me.user_id ?? me.user_claim ?? MOCK_USER.id,
    name: me.name ?? "Authenticated User",
    email: me.email ?? "",
    initials: (me.name ?? "AU")
      .split(" ")
      .map((segment) => segment[0] ?? "")
      .join("")
      .slice(0, 2)
      .toUpperCase(),
    role: "Member",
    plan: "free",
    org: me.tenant_claim ?? me.tenant_id ?? "Default",
  };
}

function AuthProvider({ children }: AuthProviderProps) {
  const [user, setUser] = useState<UserProfile | null>(null);

  // Invalidate and refetch all queries when the authenticated user changes,
  // and clear the cache when transitioning to an unauthenticated state.
  useEffect(() => {
    if (typeof window !== "undefined") {
      if (user?.id) {
        void queryClient.invalidateQueries();
      } else {
        queryClient.clear();
      }
    }
  }, [user?.id]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    let cancelled = false;
    let intervalId: ReturnType<typeof setInterval> | undefined;

    const syncSession = async () => {
      try {
        if (isNeonAuthConfigured()) {
          await initializeNeonSession();
        }
        if (!getAccessToken()) {
          if (!cancelled) setUser(null);
          return;
        }
        try {
          const me = await authEndpoints.me();
          if (cancelled) return;
          setUser(mapProfile(me));
        } catch (fetchError: unknown) {
          // During prerender, network requests to the dev server may fail.
          // Silently skip auth fetch — user will authenticate on first browser interaction.
          if (cancelled) return;
          const errorMsg = fetchError instanceof Error ? fetchError.message : String(fetchError);
          if (errorMsg.includes("ECONNREFUSED") || errorMsg.includes("ETIMEDOUT")) {
            // Expected during prerender; do not clear auth state
            return;
          }
          authEndpoints.clearLocalAuth();
          setUser(null);
        }
      } catch {
        if (cancelled) return;
        authEndpoints.clearLocalAuth();
        setUser(null);
      }
    };

    void syncSession();

    // Periodically sync session if Neon Auth is configured to support out-of-band login/logout redirect flows
    if (isNeonAuthConfigured()) {
      intervalId = setInterval(async () => {
        const oldToken = getAccessToken();
        const newToken = await initializeNeonSession();
        if (newToken !== oldToken) {
          if (newToken) {
            try {
              const me = await authEndpoints.me();
              if (!cancelled) setUser(mapProfile(me));
            } catch {
              if (!cancelled) {
                authEndpoints.clearLocalAuth();
                setUser(null);
              }
            }
          } else {
            if (!cancelled) setUser(null);
          }
        }
      }, 1500);
    }

    return () => {
      cancelled = true;
      if (intervalId) {
        clearInterval(intervalId);
      }
    };
  }, []);

  const logout = useCallback(() => {
    if (isNeonAuthConfigured() && neonAuthClient) {
      void neonAuthClient.signOut().catch(() => undefined);
    }
    authEndpoints.clearLocalAuth();
    setUser(null);
    queryClient.clear();
  }, []);

  const setPlan = useCallback((plan: PlanTier) => {
    setUser((prev) => (prev ? { ...prev, plan } : null));
  }, []);

  const refresh = useCallback(async () => {
    try {
      if (isNeonAuthConfigured()) {
        await initializeNeonSession();
      }
      const me = await authEndpoints.me();
      setUser(mapProfile(me));
    } catch {
      setUser(null);
      authEndpoints.clearLocalAuth();
    }
  }, []);

  const value: AuthContextValue = {
    isAuthenticated: user !== null,
    user,
    logout,
    setPlan,
    refresh,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export { AuthProvider };

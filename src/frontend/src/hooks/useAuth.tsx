/**
 * Mock authentication context.
 *
 * Provides login/logout state, user profile, and plan information.
 * All data is local — no real backend. The user starts logged-in by default
 * so the app demo is immediately interactive.
 */
import {
  createContext,
  useContext,
  useState,
  useCallback,
  type ReactNode,
} from "react";

// ── Types ───────────────────────────────────────────────────────────

export type PlanTier = "free" | "pro" | "enterprise";

export interface UserProfile {
  id: string;
  name: string;
  email: string;
  initials: string;
  avatarUrl?: string;
  role: string;
  plan: PlanTier;
  org: string;
}

interface AuthContextValue {
  /** Whether the user is currently authenticated */
  isAuthenticated: boolean;
  /** The current user profile (null when logged out) */
  user: UserProfile | null;
  /** Sign in with mock credentials */
  login: (email: string, password: string) => Promise<boolean>;
  /** Sign out */
  logout: () => void;
  /** Update the user's plan tier */
  setPlan: (plan: PlanTier) => void;
}

// ── Mock data ───────────────────────────────────────────────────────

const MOCK_USER: UserProfile = {
  id: "usr_01",
  name: "Alex Chen",
  email: "alex@qredence.ai",
  initials: "AC",
  role: "Skill Architect",
  plan: "pro",
  org: "Qredence",
};

// ── Context ─────────────────────────────────────────────────────────

// HMR-safe default: during hot-reload the context module re-evaluates
// and creates a new context instance while the old provider is still
// mounted. A complete default prevents the "must be used within
// <AuthProvider>" throw during the brief HMR gap.
const noopAsync = async () => false as boolean;
const noopVoid = () => {};

const defaultAuthCtx: AuthContextValue = {
  isAuthenticated: false,
  user: null,
  login: noopAsync,
  logout: noopVoid,
  setPlan: noopVoid,
};

const AuthContext = createContext<AuthContextValue>(defaultAuthCtx);

// ── Provider ────────────────────────────────────────────────────────

interface AuthProviderProps {
  children: ReactNode;
}

function AuthProvider({ children }: AuthProviderProps) {
  const [user, setUser] = useState<UserProfile | null>(MOCK_USER);

  const login = useCallback(
    async (_email: string, _password: string): Promise<boolean> => {
      // Simulate network delay
      await new Promise((r) => setTimeout(r, 800));
      setUser({ ...MOCK_USER, email: _email });
      return true;
    },
    [],
  );

  const logout = useCallback(() => {
    setUser(null);
  }, []);

  const setPlan = useCallback((plan: PlanTier) => {
    setUser((prev) => (prev ? { ...prev, plan } : null));
  }, []);

  const value: AuthContextValue = {
    isAuthenticated: user !== null,
    user,
    login,
    logout,
    setPlan,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// ── Hook ────────────────────────────────────────────────────────────

function useAuth(): AuthContextValue {
  return useContext(AuthContext);
}

export { AuthProvider, useAuth };

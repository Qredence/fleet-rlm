import type { PlanTier, UserProfile } from "@/lib/auth/types";

export type { PlanTier, UserProfile };

export interface AuthContextValue {
  isAuthenticated: boolean;
  user: UserProfile | null;
  login: (email: string, password: string) => Promise<boolean>;
  logout: () => void;
  setPlan: (plan: PlanTier) => void;
}

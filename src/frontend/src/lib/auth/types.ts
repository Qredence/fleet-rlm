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

export interface AuthContextValue {
  isAuthenticated: boolean;
  user: UserProfile | null;
  login: () => Promise<boolean>;
  logout: () => void;
  setPlan: (plan: PlanTier) => void;
}

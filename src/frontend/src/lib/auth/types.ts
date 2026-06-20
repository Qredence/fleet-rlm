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
  logout: () => void;
  setPlan: (plan: PlanTier) => void;
  refresh?: () => Promise<void>;
}

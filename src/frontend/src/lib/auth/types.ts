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

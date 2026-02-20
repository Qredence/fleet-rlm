import {
  User,
  Settings as SettingsIcon,
  CreditCard,
  Bell,
  Paintbrush,
  Database,
  Info,
} from "lucide-react";

// ── Settings categories ─────────────────────────────────────────────
export const categories = [
  { key: "account", label: "Account", icon: User },
  { key: "billing", label: "Billing", icon: CreditCard },
  { key: "general", label: "General", icon: SettingsIcon },
  { key: "notifications", label: "Notifications", icon: Bell },
  { key: "personalization", label: "Personalization", icon: Paintbrush },
  { key: "data", label: "Data & Privacy", icon: Database },
  { key: "about", label: "About", icon: Info },
] as const;

export type Category = (typeof categories)[number]["key"];

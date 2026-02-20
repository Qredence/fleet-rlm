/**
 * SettingsPaneContent — shared pane router for SettingsDialog & SettingsPage.
 *
 * This eliminates the duplicated switch statement that previously existed
 * in both consumers.
 */
import type { Category } from "@/features/settings/types";
import { GeneralPane } from "@/features/settings/GeneralPane";
import { NotificationsPane } from "@/features/settings/NotificationsPane";
import { PersonalizationPane } from "@/features/settings/PersonalizationPane";
import { DataPrivacyPane } from "@/features/settings/DataPrivacyPane";
import { AboutPane } from "@/features/settings/AboutPane";
import { AccountPane } from "@/features/settings/AccountPane";
import { BillingPane } from "@/features/settings/BillingPane";

interface SettingsPaneContentProps {
  activeCategory: Category;
  isDark: boolean;
  onToggleTheme: () => void;
}

export function SettingsPaneContent({
  activeCategory,
  isDark,
  onToggleTheme,
}: SettingsPaneContentProps) {
  switch (activeCategory) {
    case "account":
      return <AccountPane />;
    case "billing":
      return <BillingPane />;
    case "general":
      return <GeneralPane isDark={isDark} onToggleTheme={onToggleTheme} />;
    case "notifications":
      return <NotificationsPane />;
    case "personalization":
      return <PersonalizationPane />;
    case "data":
      return <DataPrivacyPane />;
    case "about":
      return <AboutPane />;
  }
}

/**
 * SettingsPaneContent — shared pane router for SettingsDialog & SettingsPage.
 *
 * This eliminates the duplicated switch statement that previously existed
 * in both consumers.
 */
import type { Category } from "./types";
import { GeneralPane } from "./GeneralPane";
import { NotificationsPane } from "./NotificationsPane";
import { PersonalizationPane } from "./PersonalizationPane";
import { DataPrivacyPane } from "./DataPrivacyPane";
import { AboutPane } from "./AboutPane";
import { AccountPane } from "./AccountPane";
import { BillingPane } from "./BillingPane";

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

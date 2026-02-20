/**
 * Re-export from the decomposed settings module.
 *
 * The monolithic SettingsDialog was split into focused sub-components:
 *
 *   features/settings/
 *     ├── SettingsDialog.tsx      — Shell (Dialog/Drawer + category nav)
 *     ├── AccountPane.tsx         — Account & team members pane
 *     ├── BillingPane.tsx         — Billing, payment methods, invoices pane
 *     ├── GeneralPane.tsx         — General settings pane
 *     ├── NotificationsPane.tsx   — Notifications pane
 *     ├── PersonalizationPane.tsx — Personalization pane
 *     ├── DataPrivacyPane.tsx     — Data & Privacy pane
 *     ├── AboutPane.tsx           — About pane
 *     ├── SettingsSelectField.tsx — Reusable select-row form element
 *     ├── SettingsToggleRow.tsx   — Reusable toggle-row form element
 *     └── types.ts                — Category definitions
 */
export { SettingsDialog } from "./settings/SettingsDialog";

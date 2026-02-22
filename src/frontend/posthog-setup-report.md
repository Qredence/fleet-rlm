# PostHog post-wizard report

The wizard has completed a deep integration of PostHog analytics into your Skill Fleet application. The integration includes automatic page view tracking, user identification on login/signup, and custom event tracking for key user actions throughout the app.

## Integration Summary

- **PostHog SDK**: Installed `posthog-js` and `@posthog/react` packages
- **Provider Setup**: PostHog initialized in `src/main.tsx` with PostHogProvider wrapping the app
- **Environment Variables**: Use canonical `VITE_PUBLIC_POSTHOG_API_KEY` and `VITE_PUBLIC_POSTHOG_HOST` in `.env` (`VITE_PUBLIC_POSTHOG_KEY` remains a temporary compatibility alias in v0.4.8)
- **Error Tracking**: Integrated `captureException` in ErrorBoundary and RouteErrorPage components
- **User Identification**: Users are identified on login/signup with email and name properties

## Tracked Events

| Event Name                        | Description                                                        | File                                                   |
| --------------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------ |
| `user_signed_up`                  | User completed the signup form and account was created             | `src/app/pages/SignupPage.tsx`                         |
| `user_logged_in`                  | User successfully signed in via the login page                     | `src/app/pages/LoginPage.tsx`                          |
| `user_logged_out`                 | User initiated logout and session was ended                        | `src/app/pages/LogoutPage.tsx`                         |
| `skill_creation_started`          | User submitted their first message to begin creating a new skill   | `src/app/pages/skill-creation/SkillCreationFlow.tsx`   |
| `skill_selected`                  | User clicked on a skill card to view its details                   | `src/app/pages/SkillLibrary.tsx`                       |
| `skill_search_performed`          | User performed a search in the skill library                       | `src/app/pages/SkillLibrary.tsx`                       |
| `skill_filter_applied`            | User filtered skills by domain category                            | `src/app/pages/SkillLibrary.tsx`                       |
| `skill_created`                   | A new skill was successfully created via the mutation              | `src/app/components/hooks/useSkillMutations.ts`        |
| `skill_deleted`                   | A skill was successfully deleted via the mutation                  | `src/app/components/hooks/useSkillMutations.ts`        |
| `conversation_saved`              | Chat conversation was auto-saved when starting a new session       | `src/app/pages/skill-creation/SkillCreationFlow.tsx`   |
| `plan_upgraded`                   | User upgraded from one plan tier to another (e.g., Free to Pro)    | `src/app/components/features/PricingDialog.tsx`        |
| `plan_downgraded`                 | User downgraded from a higher plan tier to a lower one             | `src/app/components/features/PricingDialog.tsx`        |
| `enterprise_inquiry_submitted`    | User clicked to contact sales for Enterprise plan                  | `src/app/components/features/PricingDialog.tsx`        |
| `integration_connected`           | User connected an external integration (GitHub, Slack, etc.)       | `src/app/components/features/IntegrationsDialog.tsx`   |
| `integration_disconnected`        | User disconnected an external integration                          | `src/app/components/features/IntegrationsDialog.tsx`   |
| `command_palette_opened`          | User opened the command palette via keyboard shortcut              | `src/app/components/features/CommandPalette.tsx`       |
| `command_palette_action_selected` | User selected an action from the command palette                   | `src/app/components/features/CommandPalette.tsx`       |
| `hitl_checkpoint_resolved`        | User resolved a human-in-the-loop checkpoint during skill creation | `src/app/pages/skill-creation/useChatSimulation.ts`    |
| `theme_toggled`                   | User toggled between dark and light mode                           | `src/app/components/hooks/useTheme.ts`                 |
| `settings_opened`                 | User opened the settings dialog from user menu                     | `src/app/components/features/UserMenu.tsx`             |
| `payment_method_added`            | User initiated adding a new payment method                         | `src/app/components/features/settings/BillingPane.tsx` |
| `invoice_downloaded`              | User downloaded a billing invoice                                  | `src/app/components/features/settings/BillingPane.tsx` |

## Next steps

We've built some insights and a dashboard for you to keep an eye on user behavior, based on the events we just instrumented:

### Dashboard

- [Analytics basics](https://eu.posthog.com/project/15008/dashboard/531074) - Core user analytics dashboard with conversion funnels and engagement metrics

### Insights

- [Login to Plan Upgrade Funnel](https://eu.posthog.com/project/15008/insights/SsUsIcK8) - Conversion funnel tracking users from login to plan upgrade
- [User Authentication Activity](https://eu.posthog.com/project/15008/insights/CXOZqetS) - Daily trend of logins, signups, and logouts
- [Integration Connections](https://eu.posthog.com/project/15008/insights/3HbECAgZ) - Track integration connect/disconnect events
- [Feature Engagement](https://eu.posthog.com/project/15008/insights/G3gfwvW5) - Command palette, settings, and theme toggle usage
- [Plan Changes & Revenue Events](https://eu.posthog.com/project/15008/insights/s7ppj1sw) - Weekly plan upgrades, downgrades, and enterprise inquiries

### Agent skill

We've left an agent skill folder in your project at `.claude/skills/posthog-integration-react-react-router-7-data/`. You can use this context for further agent development when using Claude Code. This will help ensure the model provides the most up-to-date approaches for integrating PostHog.

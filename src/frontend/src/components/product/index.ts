// Product-level reusable composed components.
//
// These are app-specific patterns used across 2+ feature surfaces.
// They sit between low-level primitives (components/ui) and
// feature-specific UI (features/{feature}/ui).
export { PageHeader } from "./page-header";
export { PropertyList, PropertyItem, PropertyGroup } from "./property-list";
export { EmptyPanel } from "./empty-panel";
export { ErrorBoundary } from "./error-boundary";
export { PageSkeleton, PanelSkeleton } from "./page-skeleton";
export { StatusBadge, StatusIndicator, StatusMessage, ExecutionProgress } from "./execution-status";
export {
  Section,
  SectionHeader,
  SectionContent,
  SectionCard,
  ContentArea,
  MainRegion,
  FooterRegion,
  HeaderRegion,
} from "./section-layout";
export { TimelineStep } from "./timeline";
export type { TimelineStepProps, TimelineStepStatus } from "./timeline";

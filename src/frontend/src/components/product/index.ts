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
  SectionCardHeader,
  SectionCardTitle,
  SectionCardDescription,
  SectionCardContent,
  SectionCardFooter,
  ContentArea,
  MainRegion,
  FooterRegion,
  HeaderRegion,
} from "./section-layout";
export { TimelineStep } from "./timeline";
export type { TimelineStepProps, TimelineStepStatus } from "./timeline";
export { DataTable } from "./data-table";
export type { ColumnDef, SortState, SortDirection, DataTableProps } from "./data-table";
export { DetailDrawer } from "./detail-drawer";
export type { DetailDrawerProps } from "./detail-drawer";
export { ScoreBadge } from "./score-badge";
export type { ScoreBadgeProps } from "./score-badge";
export { DiffViewer } from "./diff-viewer";
export type { DiffMode, DiffViewerProps } from "./diff-viewer";
export { FilePreview } from "./file-preview";
export type { FilePreviewProps } from "./file-preview";
export { ChartSparkline } from "./chart-sparkline";
export type { ChartSparklineProps } from "./chart-sparkline";
export { StateNotice } from "./state-notice";
export { KeyValueGrid } from "./key-value-grid";
export { TextShimmer } from "./text-shimmer";

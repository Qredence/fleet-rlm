/**
 * Domain Components
 *
 * Domain-specific components for artifacts and other specialized UI.
 */

// Artifacts subdirectory
export { ArtifactCanvas, type ArtifactTab } from "./artifacts/ArtifactCanvas";
export { ArtifactGraph } from "./artifacts/ArtifactGraph";
export { ArtifactPreview } from "./artifacts/ArtifactPreview";
export { ArtifactREPL } from "./artifacts/ArtifactREPL";
export { ArtifactTimeline } from "./artifacts/ArtifactTimeline";
export { GraphStepNode, type GraphStepNodeData } from "./artifacts/GraphStepNode";
export { NODE_WIDTH, STEP_TYPE_META } from "./artifacts/GraphStepNode.constants";

// Artifact parsers
export {
  TrajectoryStepSchema,
  TrajectoryEnvelopeSchema,
  ToolPayloadSchema,
  ErrorPayloadSchema,
  FinalOutputPayloadEnvelopeSchema,
  type ParsedArtifactPayload,
  parseArtifactPayload,
  parseFinalOutputEnvelope,
} from "./artifacts/parsers/artifactPayloadSchemas";
export { asText, summarizeArtifactStep, type ArtifactPreviewModel, buildArtifactPreviewModel } from "./artifacts/parsers/artifactPayloadSummaries";
export { extractReplCodePreview, extractErrorDetails, extractTrajectoryChain } from "./artifacts/graphNodeDetailParsers";
export { extractToolBadgeFromStep } from "./artifacts/graphToolBadge";

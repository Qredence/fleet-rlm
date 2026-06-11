export function normalizeTrackingUri(trackingUri: string): string {
  return trackingUri.trim().replace(/\/$/, "");
}

export function buildMlflowTraceUrl(args: {
  trackingUri: string;
  traceId: string;
  experimentId?: string | null;
}): string {
  const base = normalizeTrackingUri(args.trackingUri || "http://127.0.0.1:5001");
  const traceId = args.traceId.trim();
  const experimentId = args.experimentId?.trim();
  if (experimentId) {
    return `${base}/#/experiments/${encodeURIComponent(experimentId)}/traces/${encodeURIComponent(traceId)}`;
  }
  return `${base}/#/traces?search=${encodeURIComponent(traceId)}`;
}

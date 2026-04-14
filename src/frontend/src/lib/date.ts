const ISO_TIMESTAMP_WITH_ZONE = /(?:Z|[+-]\d{2}:\d{2})$/;

export function parseIsoTimestamp(iso: string): Date {
  const normalized = ISO_TIMESTAMP_WITH_ZONE.test(iso) ? iso : `${iso}Z`;
  return new Date(normalized);
}

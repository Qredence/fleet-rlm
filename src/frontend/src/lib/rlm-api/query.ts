/**
 * Shared query-string helper for fleet-rlm API endpoint modules.
 *
 * Skips null/undefined/empty values and appends a `?` only when at least one
 * parameter is present. Use this instead of hand-rolling `URLSearchParams` in
 * feature code or endpoint modules.
 */
export function withQuery(
  path: string,
  params: Record<string, string | number | boolean | null | undefined>,
): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    search.set(key, String(value));
  }
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}

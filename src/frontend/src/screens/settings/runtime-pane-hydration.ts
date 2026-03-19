export function shouldHydrateRuntimeForm(
  snapshot: { values?: Record<string, string> } | undefined,
  hasUnsavedRuntimeChanges: boolean,
): boolean {
  return Boolean(snapshot) && !hasUnsavedRuntimeChanges;
}

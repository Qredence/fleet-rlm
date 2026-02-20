let localIdSequence = 0;

export function createLocalId(prefix: string): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `${prefix}-${crypto.randomUUID()}`;
  }

  localIdSequence += 1;
  return `${prefix}-${Date.now()}-${localIdSequence}`;
}

export function humanizeLabel(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return trimmed;
  return trimmed
    .replace(/^tool:\s*/i, "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .replace(/^\w/, (letter) => letter.toUpperCase());
}

export function uniqueStrings(values: string[]) {
  return [...new Set(values.filter(Boolean))];
}

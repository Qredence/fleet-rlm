export interface FuzzyMatchResult {
  item: string;
  score: number;
  indices: number[];
}

export interface FuzzySearchResult<T> {
  item: T;
  score: number;
  indices: number[];
}

export interface HighlightSegment {
  text: string;
  isMatch: boolean;
}

export function fuzzyMatch(query: string, target: string): FuzzyMatchResult {
  if (query.length === 0) {
    return { item: target, score: 100, indices: [] };
  }
  const queryLower = query.toLowerCase();
  const targetLower = target.toLowerCase();
  const indices: number[] = [];
  let queryIdx = 0;
  for (let i = 0; i < target.length && queryIdx < query.length; i++) {
    if (targetLower[i] === queryLower[queryIdx]) {
      indices.push(i);
      queryIdx++;
    }
  }
  if (queryIdx < query.length) {
    return { item: target, score: 0, indices: [] };
  }
  return { item: target, score: 50 + indices.length * 5, indices };
}

export function fuzzySearch<T>(
  query: string,
  items: T[],
  getSearchableText: (item: T) => string
): FuzzySearchResult<T>[] {
  const results: FuzzySearchResult<T>[] = [];
  for (const item of items) {
    const text = getSearchableText(item);
    const match = fuzzyMatch(query, text);
    if (match.score > 0) {
      results.push({ item, score: match.score, indices: match.indices });
    }
  }
  return results.sort((a, b) => b.score - a.score);
}

export function highlightMatches(text: string, indices: number[]): HighlightSegment[] {
  if (indices.length === 0) {
    return [{ text, isMatch: false }];
  }
  const segments: HighlightSegment[] = [];
  const sorted = [...indices].sort((a, b) => a - b);
  let last = 0;
  for (const idx of sorted) {
    if (idx > last) {
      segments.push({ text: text.slice(last, idx), isMatch: false });
    }
    segments.push({ text: text[idx] || '', isMatch: true });
    last = idx + 1;
  }
  if (last < text.length) {
    segments.push({ text: text.slice(last), isMatch: false });
  }
  return segments;
}

/**
 * Ripgrep-like search component for OpenTUI
 * Fast file content searching with regex support
 */

import { useEffect, useState, useCallback } from "react";
import { z } from "zod";
import { Ripgrep, type RipgrepResult } from "../file/ripgrep";

// Zod schema for search options
export const SearchOptionsSchema = z.object({
  pattern: z.string(),
  path: z.string().optional(),
  caseSensitive: z.boolean().optional(),
  wholeWord: z.boolean().optional(),
  regex: z.boolean().optional(),
  include: z.array(z.string()).optional(),
  exclude: z.array(z.string()).optional(),
  maxResults: z.number().positive().optional(),
  contextLines: z.number().nonnegative().optional(),
});

export type SearchOptions = z.infer<typeof SearchOptionsSchema>;

// Search result type
export interface SearchResult {
  filePath: string;
  lineNumber: number;
  column: number;
  line: string;
  matches: Array<{
    start: number;
    end: number;
    text: string;
  }>;
  context?: {
    before: string[];
    after: string[];
  };
}

// Search state
export interface SearchState {
  results: SearchResult[];
  isSearching: boolean;
  error: string | null;
  totalFiles: number;
  searchedFiles: number;
}

// Search props
export interface SearchProps {
  options: SearchOptions;
  onResult?: (result: SearchResult) => void;
  onComplete?: (results: SearchResult[]) => void;
  onError?: (error: string) => void;
}

// Escape regex special characters
function escapeRegExp(string: string): string {
  return string.replace(/[.*+?^${}()|[\]\\]/g, "\\$\u0026");
}

// Build search regex from options
function buildSearchRegex(options: SearchOptions): RegExp {
  let pattern = options.pattern;
  
  if (!options.regex) {
    pattern = escapeRegExp(pattern);
  }
  
  if (options.wholeWord) {
    pattern = `\\b${pattern}\\b`;
  }
  
  const flags = options.caseSensitive ? "g" : "gi";
  
  return new RegExp(pattern, flags);
}

// Check if file matches include/exclude patterns
function shouldIncludeFile(filePath: string, include?: string[], exclude?: string[]): boolean {
  if (exclude) {
    for (const pattern of exclude) {
      const regex = new RegExp(pattern.replace(/\*/g, ".*").replace(/\?/g, "."));
      if (regex.test(filePath)) {
        return false;
      }
    }
  }
  
  if (include) {
    for (const pattern of include) {
      const regex = new RegExp(pattern.replace(/\*/g, ".*").replace(/\?/g, "."));
      if (regex.test(filePath)) {
        return true;
      }
    }
    return false;
  }
  
  return true;
}

// Search in text content
function searchInText(
  content: string,
  filePath: string,
  regex: RegExp,
  contextLines: number = 0
): SearchResult[] {
  const results: SearchResult[] = [];
  const lines = content.split("\n");
  
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] ?? "";
    const matches: SearchResult["matches"] = [];
    
    let match;
    while ((match = regex.exec(line)) !== null) {
      matches.push({
        start: match.index,
        end: match.index + match[0].length,
        text: match[0],
      });
    }
    
    if (matches.length > 0) {
      const firstMatch = matches[0]!;
      const result: SearchResult = {
        filePath,
        lineNumber: i + 1,
        column: (firstMatch?.start ?? 0) + 1,
        line,
        matches,
      };
      
      if (contextLines > 0) {
        result.context = {
          before: lines.slice(Math.max(0, i - contextLines), i).filter((l): l is string => l !== undefined),
          after: lines.slice(i + 1, Math.min(lines.length, i + 1 + contextLines)).filter((l): l is string => l !== undefined),
        };
      }
      
      results.push(result);
    }
    
    // Reset regex lastIndex for next line
    regex.lastIndex = 0;
  }
  
  return results;
}

// Highlight matches in a line
export function highlightMatches(line: string, matches: SearchResult["matches"]): string {
  let result = "";
  let lastEnd = 0;
  
  for (const match of matches) {
    result += line.slice(lastEnd, match.start);
    result += `\x1b[1;31m${match.text}\x1b[0m`;
    lastEnd = match.end;
  }
  
  result += line.slice(lastEnd);
  return result;
}

// Format result for display
export function formatSearchResult(result: SearchResult, showContext: boolean = true): string {
  const lines: string[] = [];
  
  // File path and line number
  lines.push(`\x1b[36m${result.filePath}\x1b[0m:\x1b[32m${result.lineNumber}\x1b[0m:\x1b[32m${result.column}\x1b[0m`);
  
  // Context before
  if (showContext && result.context?.before) {
    for (let i = 0; i < result.context.before.length; i++) {
      const lineNum = result.lineNumber - result.context.before.length + i;
      lines.push(`\x1b[90m${lineNum.toString().padStart(6)}│ ${result.context.before[i]}\x1b[0m`);
    }
  }
  
  // Match line with highlights
  lines.push(`\x1b[32m${result.lineNumber.toString().padStart(6)}│\x1b[0m ${highlightMatches(result.line, result.matches)}`);
  
  // Context after
  if (showContext && result.context?.after) {
    for (let i = 0; i < result.context.after.length; i++) {
      const lineNum = result.lineNumber + 1 + i;
      lines.push(`\x1b[90m${lineNum.toString().padStart(6)}│ ${result.context.after[i]}\x1b[0m`);
    }
  }
  
  return lines.join("\n");
}

// React hook for search functionality
export function useSearch() {
  const [state, setState] = useState<SearchState>({
    results: [],
    isSearching: false,
    error: null,
    totalFiles: 0,
    searchedFiles: 0,
  });

  const search = useCallback(async (options: SearchOptions) => {
    setState((prev) => ({
      ...prev,
      isSearching: true,
      error: null,
      results: [],
      searchedFiles: 0,
    }));

    try {
      const results: SearchResult[] = [];
      const fileResults = new Map<string, SearchResult>();

      const generator = Ripgrep.search(options.pattern, options.path || ".", {
        caseSensitive: options.caseSensitive,
        wholeWord: options.wholeWord,
        regex: options.regex,
        include: options.include,
        exclude: options.exclude,
        maxResults: options.maxResults ?? 100,
        contextLines: options.contextLines ?? 0,
      });

      for await (const result of generator) {
        if (result.type === "match") {
          const { path, line_number, lines, submatches } = result.data;
          const filePath = path.text;

          if (!fileResults.has(filePath)) {
            fileResults.set(filePath, {
              filePath,
              lineNumber: line_number,
              column: (submatches[0]?.start ?? 0) + 1,
              line: lines.text,
              matches: submatches.map((m) => ({
                start: m.start,
                end: m.end,
                text: m.match.text,
              })),
            });
          } else {
            const existing = fileResults.get(filePath)!;
            existing.matches.push(
              ...submatches.map((m) => ({
                start: m.start,
                end: m.end,
                text: m.match.text,
              }))
            );
          }

          results.push({
            filePath,
            lineNumber: line_number,
            column: (submatches[0]?.start ?? 0) + 1,
            line: lines.text,
            matches: submatches.map((m) => ({
              start: m.start,
              end: m.end,
              text: m.match.text,
            })),
          });
        }
      }

      setState((prev) => ({
        ...prev,
        results,
        isSearching: false,
        searchedFiles: fileResults.size,
        totalFiles: fileResults.size,
      }));

      return results;
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "Unknown error";
      setState((prev) => ({
        ...prev,
        isSearching: false,
        error: errorMessage,
      }));
      throw error;
    }
  }, []);

  const clearResults = useCallback(() => {
    setState((prev) => ({
      ...prev,
      results: [],
      error: null,
    }));
  }, []);

  return {
    ...state,
    search,
    clearResults,
  };
}

// Search component for OpenTUI
export function Search({ options }: SearchProps) {
  const { results, isSearching, error, search } = useSearch();
  
  useEffect(() => {
    search(options);
  }, [options, search]);
  
  if (isSearching) {
    return (
      <box flexDirection="row" alignItems="center" gap={1}>
        <text>🔍 Searching...</text>
      </box>
    );
  }
  
  if (error) {
    return (
      <box>
        <text fg="#ff4444">❌ Error: {error}</text>
      </box>
    );
  }
  
  if (results.length === 0) {
    return (
      <box>
        <text fg="#666666">No results found</text>
      </box>
    );
  }
  
  return (
    <box flexDirection="column" gap={1}>
      <text>Found {results.length} result{results.length !== 1 ? "s" : ""}:</text>
      {results.map((result, i) => (
        <box key={i} paddingLeft={2}>
          <text>{formatSearchResult(result)}</text>
        </box>
      ))}
    </box>
  );
}

// Search input component
export function SearchInput({
  onSearch,
  placeholder = "Search...",
}: {
  onSearch: (options: SearchOptions) => void;
  placeholder?: string;
}) {
  const [pattern, setPattern] = useState("");
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [regex, setRegex] = useState(false);
  
  const handleSubmit = () => {
    if (pattern.trim()) {
      onSearch({
        pattern: pattern.trim(),
        caseSensitive,
        regex,
      });
    }
  };
  
  return (
    <box flexDirection="column" gap={1}>
      <box flexDirection="row" gap={1}>
        <input
          value={pattern}
          onChange={setPattern}
          onSubmit={handleSubmit}
          placeholder={placeholder}
          flexGrow={1}
        />
        <box flexDirection="row" gap={1}>
          <button onClick={() => setCaseSensitive(!caseSensitive)}>
            {caseSensitive ? "Aa" : "aa"}
          </button>
          <button onClick={() => setRegex(!regex)}>
            {regex ? ".*" : "ab"}
          </button>
        </box>
      </box>
    </box>
  );
}

export default Search;

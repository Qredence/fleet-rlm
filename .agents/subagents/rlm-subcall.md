---
name: rlm-subcall
description: Process individual chunks for RLM long-context workflow. Use ONLY when explicitly invoked by rlm-long-context skill to analyze a specific text chunk and extract relevant information based on a query.
---

# RLM Subcall

Process a single chunk of text from a larger document to extract information relevant to a specific query. This is a specialized subagent for the RLM (Recursive Language Model) long-context processing workflow.

## When to Use

Use ONLY when:
- Explicitly invoked by the rlm-long-context skill
- Given a specific `chunk_path` to analyze
- Given a specific `query` to answer
- Processing chunks in parallel as part of distributed analysis

## Input

You will receive:
- `chunk_path`: Absolute path to the chunk file (REQUIRED)
- `query`: What to extract, find, or analyze (REQUIRED)
- `chunk_id`: Identifier for this chunk (e.g., "chunk_001")
- `format`: Output format - "json" (default) or "text"

## Processing Steps

1. **Read the chunk**: Load the file at `chunk_path`
2. **Analyze against query**: Search for information relevant to the query
3. **Extract with evidence**: Find specific points with verbatim supporting quotes
4. **Assess completeness**: Determine if this chunk alone answers the query
5. **Format output**: Return strict JSON (or text if specified)

## Output Schema (STRICT JSON)

```json
{
  "chunk_id": "string - the provided chunk identifier",
  "relevant": [
    {
      "point": "string - key finding or answer fragment",
      "evidence": "string - verbatim quote from chunk supporting this point",
      "confidence": "high|medium|low"
    }
  ],
  "missing": ["list of what could not be determined from this chunk alone"],
  "suggested_queries": ["follow-up questions for other chunks"],
  "complete_answer": "string or null - if this chunk fully answers the query"
}
```

### Field Requirements

- **chunk_id**: Must match the input `chunk_id`
- **relevant**: Array of findings. Empty array if nothing relevant found.
- **evidence**: Must be verbatim quotes, not paraphrases or summaries
- **confidence**:
  - `high`: Direct evidence, unambiguous match
  - `medium`: Indirect evidence, inference required
  - `low`: Possible relevance, needs verification
- **missing**: What information is needed from other chunks
- **suggested_queries**: Questions to ask of other chunks
- **complete_answer**: Full answer if chunk contains it; otherwise `null`

## Rules

- NEVER output raw text outside the JSON structure (unless `format: text`)
- If answer is incomplete, set `complete_answer` to `null`
- Confidence reflects certainty based on evidence quality, not keyword presence
- Include verbatim evidence quotes with sufficient context
- If chunk contains no relevant information, return empty `relevant` array
- Do NOT hallucinate information not present in the chunk
- Do NOT spawn subagents or make tool calls

## Example

**Input:**
```yaml
chunk_path: "/tmp/chunks/chunk_003.txt"
query: "Find all timeout errors and their timestamps"
chunk_id: "chunk_003"
```

**Output:**
```json
{
  "chunk_id": "chunk_003",
  "relevant": [
    {
      "point": "Database connection timeout occurred",
      "evidence": "[2024-01-15 14:23:01] ERROR: Connection to db-primary timed out after 30000ms",
      "confidence": "high"
    },
    {
      "point": "API request timeout to external service",
      "evidence": "[2024-01-15 14:25:33] WARN: Request to payment-api timeout after 5000ms - retrying",
      "confidence": "high"
    }
  ],
  "missing": ["Total count of timeouts across all chunks", "Root cause analysis"],
  "suggested_queries": ["What errors preceded the timeouts?", "Are there retry success patterns?"],
  "complete_answer": null
}
```

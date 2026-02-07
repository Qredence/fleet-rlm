# RLM Workflow Examples

Complete examples of using the RLM workflow for different types of large-context tasks.

## Example 1: Analyzing Large Log Files

**Scenario:** User provides a 5MB application log file and wants all critical errors with context.

**Query:** "Extract all CRITICAL errors from app.log with timestamps and surrounding context"

### Workflow

```bash
# 1. Initialize
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py init /path/to/app.log

# 2. Scout the structure
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py exec -c "print(peek(0, 1000))"
# Output shows: [2024-01-23 10:15:32] INFO ...
# Logs are timestamp-prefixed, line-based

# 3. Search for critical errors
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py exec <<'PY'
critical = grep(r'CRITICAL', max_matches=100, window=200)
print(f"Found {len(critical)} CRITICAL entries")
print("First 3:")
for i, hit in enumerate(critical[:3]):
    print(f"\n{i}. {hit['snippet'][:200]}...")
PY

# 4. If too many hits, chunk the file
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py exec <<'PY'
paths = write_chunks('.letta/rlm_chunks', size=150000, overlap=5000)
print(f"Created {len(paths)} chunks")
PY

# 5. Process each chunk with subagent
```

**Subagent prompt template:**
```
Read: {chunk_path}

Task: Extract all CRITICAL errors.

For each error, return JSON:
{
  "timestamp": "...",
  "message": "...",
  "surrounding_lines": "..." (3 lines before and after)
}

Return: {"errors": [...]}
```

**Synthesis:**
- Collect all error objects from subagent responses
- Sort by timestamp
- Group by error type/pattern if requested
- Present summary + full list

---

## Example 2: Extracting Structured Data from Transcripts

**Scenario:** 200-page meeting transcript, user wants all action items and decisions.

**Query:** "Extract all action items and decisions from transcript.txt"

### Workflow

```bash
# 1. Initialize
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py init transcript.txt

# 2. Check format
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py exec -c "print(peek(0, 2000))"
# Transcript has speaker labels: "Alice: ...", "Bob: ..."

# 3. Search for keywords
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py exec <<'PY'
action_words = grep(r'\b(action item|TODO|will do|responsible for)\b', max_matches=50, flags=2)
decision_words = grep(r'\b(decided|decision|we\'ll go with|agreed)\b', max_matches=50, flags=2)
print(f"Action keywords: {len(action_words)}")
print(f"Decision keywords: {len(decision_words)}")
PY

# 4. Semantic chunking (by speaker turns or time markers)
# Or character-based if no clear structure
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py exec <<'PY'
paths = write_chunks('.letta/rlm_chunks', size=180000, overlap=8000)
print(f"{len(paths)} chunks created")
PY

# 5. Process chunks
```

**Subagent prompt:**
```
Read: {chunk_path}

Extract:
1. Action items (who, what, when)
2. Decisions made (what was decided, context)

Return JSON:
{
  "action_items": [
    {"person": "...", "task": "...", "deadline": "..."}
  ],
  "decisions": [
    {"decision": "...", "context": "...", "speaker": "..."}
  ]
}

Only include clear, explicit items. Skip vague mentions.
```

**Synthesis:**
- Deduplicate similar action items across chunks
- Organize by person/team
- Present decisions chronologically
- Flag any incomplete information

---

## Example 3: Summarizing Documentation

**Scenario:** 1000-page technical specification, user wants high-level summary and specific section.

**Query:** "Summarize the authentication section from spec.md"

### Workflow

```bash
# 1. Initialize
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py init spec.md

# 2. Find authentication section
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py exec <<'PY'
auth_hits = grep(r'^## Authentication', max_matches=5, window=500)
if auth_hits:
    match = auth_hits[0]
    print(f"Found at position: {match['span']}")
    print(match['snippet'])
PY

# 3. Extract just authentication section
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py exec <<'PY'
# Find section boundaries (between ## headers)
import re
headers = [(m.start(), m.group(0)) for m in re.finditer(r'^## .+$', content, re.MULTILINE)]

# Find authentication header
auth_idx = None
for i, (pos, title) in enumerate(headers):
    if 'Authentication' in title:
        auth_idx = i
        break

if auth_idx is not None:
    start = headers[auth_idx][0]
    end = headers[auth_idx + 1][0] if auth_idx + 1 < len(headers) else len(content)
    auth_section = content[start:end]

    # Write to separate file
    from pathlib import Path
    out = Path('.letta/rlm_chunks/auth_section.txt')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(auth_section)
    print(f"Extracted {len(auth_section):,} chars to {out}")
PY

# 4. If section is still large, chunk it
# Otherwise, process directly with subagent
```

**Subagent prompt:**
```
Read: .letta/rlm_chunks/auth_section.txt

Create a comprehensive summary of the authentication approach:

1. Overview (2-3 sentences)
2. Key components (bullet list)
3. Authentication flow (step-by-step)
4. Important notes/warnings

Format as markdown. Be thorough but concise.
```

---

## Example 4: Finding Patterns in Scraped Web Content

**Scenario:** Scraped content from 500 web pages (single concatenated file), find all mentions of pricing.

**Query:** "Find all pricing information from scraped_pages.txt"

### Workflow

```bash
# 1. Initialize
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py init scraped_pages.txt

# 2. Quick search for price patterns
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py exec <<'PY'
import re
price_patterns = [
    r'\$\d+(?:,\d{3})*(?:\.\d{2})?',  # $123.45
    r'\d+(?:,\d{3})*(?:\.\d{2})?\s*(?:USD|dollars)',  # 123 dollars
    r'(?:price|cost|pricing):\s*\$?\d+',  # price: $50
]

all_prices = []
for pattern in price_patterns:
    hits = grep(pattern, max_matches=100, window=150, flags=2)
    all_prices.extend(hits)

print(f"Found {len(all_prices)} price mentions")
print("\nSample:")
for hit in all_prices[:5]:
    print(f"  {hit['match']} -> ...{hit['snippet'][:100]}...")
PY

# 3. Chunk for detailed extraction
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py exec <<'PY'
paths = write_chunks('.letta/rlm_chunks', size=200000, overlap=5000)
print(f"Created {len(paths)} chunks")
PY

# 4. Process with subagent
```

**Subagent prompt:**
```
Read: {chunk_path}

Extract all pricing information.

For each price found, return:
{
  "price": "...",
  "currency": "...",
  "context": "..." (what is being priced),
  "page_title": "..." (if identifiable)
}

Return: {"prices": [...]}

Focus on explicit pricing. Skip vague mentions.
```

**Synthesis:**
- Deduplicate identical price mentions
- Group by product/service
- Create price range summary
- Flag any ambiguous/conflicting pricing

---

## Example 5: Multi-Pass Analysis

**Scenario:** Large dataset where you need to first identify patterns, then extract based on those patterns.

**Pass 1: Discover pattern types**

```bash
# Sample randomly from content
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py exec <<'PY'
import random
n = len(content)
samples = [
    peek(random.randint(0, n-5000), random.randint(0, n-5000)+5000)
    for _ in range(5)
]
for i, s in enumerate(samples):
    print(f"\n=== Sample {i} ===")
    print(s[:500])
PY
```

Use subagent to analyze samples and identify patterns.

**Pass 2: Extract based on discovered patterns**

```bash
# Chunk with strategy informed by Pass 1
python3 ~/.letta/skills/rlm/scripts/rlm_repl.py exec <<'PY'
# Use pattern-specific chunking
# Then write chunks
PY
```

Process chunks with updated extraction logic.

---

## Tips for All Workflows

1. **Start with reconnaissance**: Use `peek()` and `grep()` to understand structure before chunking
2. **Choose appropriate chunk size**:
   - Smaller (100k) for dense, important content
   - Larger (250k) for sparse, repetitive content
3. **Add overlap for safety**: 5-10k chars prevents losing context at boundaries
4. **Validate early**: Process 1-2 chunks first, check quality before processing all
5. **Structured output**: Always request JSON or structured format from subagents
6. **Incremental synthesis**: Don't wait for all chunks - synthesize partial results as you go
7. **Clean up**: Delete `.letta/rlm_chunks/` and state files when done

## Common Patterns

### Pattern: Frequency Analysis
1. Quick grep to count occurrences
2. If high count, chunk and aggregate
3. Present sorted frequency table

### Pattern: Timeline Extraction
1. Chunk by time boundaries (if logs/events)
2. Extract chronological events from each chunk
3. Merge into master timeline
4. Sort and filter by date range

### Pattern: Hierarchical Summary
1. Chunk by document structure (sections/chapters)
2. Summarize each chunk individually
3. Create high-level summary from chunk summaries
4. Present multi-level summary (overview → details)

### Pattern: Entity Extraction
1. Sample to identify entity types
2. Chunk content
3. Extract entities from each chunk
4. Deduplicate and normalize
5. Present entity catalog with frequencies

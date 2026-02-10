---
name: suggest-rlm-for-document-processing
enabled: true
event: prompt
conditions:
  - field: user_prompt
    operator: regex_match
    pattern: (process|analyze|extract|read|parse).{0,30}(document|file|pdf|paper|report|log|text|content)
---

📊 **Document processing task detected!**

You want to process or analyze a document. The fleet-rlm team specializes in long-context processing:

**Recommended approach:**
1. **Use `/rlm-run`** - Execute RLM task with proper configuration
2. **Spawn `rlm-orchestrator`** - For complex multi-step analysis

**What RLM can do:**
- Extract information from large documents
- Parallel analysis with `llm_query_batched`
- Synthesize findings across multiple chunks
- Handle files >100K lines efficiently

**Example workflow:**
```
Spawn rlm-orchestrator to process <file> and extract <information>
```

**Alternative:** Use `/rlm` skill directly for simpler tasks.

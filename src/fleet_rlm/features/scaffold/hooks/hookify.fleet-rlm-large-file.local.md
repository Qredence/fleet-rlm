---
name: suggest-rlm-for-large-files
enabled: true
event: prompt
conditions:
  - field: user_prompt
    operator: regex_match
    pattern: (file|document|text|log).*?(large|big|huge|massive|>\s*100|100\s*KB|100KB|MB|GB)
---

📄 **Large file detected!**

You mentioned a large file or document. Consider using the fleet-rlm team for efficient long-context processing:

**Options:**
- `/rlm` - Main skill for long-context RLM tasks
- `/rlm-run` - Run RLM tasks with proper configuration
- Spawn `rlm-orchestrator` agent for complex document analysis

**Why use RLM?**
- Processes files >100K lines efficiently
- Uses recursive LLM calls for semantic analysis
- Prevents context window pollution

---
name: trigger-rlm-debug-for-llm-query-errors
enabled: true
event: prompt
conditions:
  - field: user_prompt
    operator: regex_match
    pattern: (llm_query|llm_query_batched|sub_llm).*?(error|failed|not defined|undefined|NameError|not working)
---

🔧 **RLM tool error detected!**

You mentioned an issue with `llm_query` or related RLM tools. The fleet-rlm team can help:

**Immediate actions:**
- `/rlm-debug` - Debug RLM execution issues
- Spawn `rlm-specialist` agent for advanced debugging

**Common fixes:**
- Ensure driver.py has latest changes
- Verify `llm_query` is injected into sandbox globals
- Check `max_llm_calls` limit not exceeded
- Validate `sub_lm` configuration

**Debug checklist:**
1. Run `modal-interpreter-agent` to check setup
2. Verify `.env` has valid API credentials
3. Check volume is accessible (V2 recommended)

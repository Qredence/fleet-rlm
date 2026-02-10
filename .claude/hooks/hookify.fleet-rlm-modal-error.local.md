---
name: trigger-rlm-debug-for-modal-errors
enabled: true
event: prompt
conditions:
  - field: user_prompt
    operator: regex_match
    pattern: (modal|sandbox).*?(error|failed|failure|not working|broken|issue|problem)
---

🚨 **Modal/Sandbox issue detected!**

You mentioned a Modal or sandbox error. The fleet-rlm team can help diagnose and fix this:

**Quick fixes:**
- `/rlm-debug` - Debug RLM execution issues
- `/modal-sandbox` - Manage Modal sandboxes

**Or spawn the specialist:**
- `modal-interpreter-agent` - Diagnose Modal sandbox issues
- `rlm-specialist` - Debug and optimize RLM workflows

**Common issues:**
- Credentials not configured
- Volume not accessible
- Sandbox timeout

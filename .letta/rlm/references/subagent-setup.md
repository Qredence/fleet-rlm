# Setting Up the rlm-subcall Subagent

This guide explains how to create a dedicated `rlm-subcall` subagent for use with the RLM workflow.

## Why a Dedicated Subagent?

The `rlm-subcall` agent is optimized for:
- Analyzing individual chunks of content
- Extracting structured information
- Returning concise, focused results
- Operating with minimal context overhead

## Option 1: Using Letta Task Tool (Recommended)

The simplest approach is to use Letta's built-in Task tool without creating a dedicated agent:

```python
# Spawn a one-off subagent for each chunk
Task({
    "subagent_type": "general-purpose",
    "description": f"Extract errors from chunk {i}",
    "prompt": f"""Read and analyze: {chunk_path}

Task: {user_query}

Return structured JSON with your findings.
Be concise - extract and summarize, don't dump raw content."""
})
```

**Advantages:**
- No setup required
- Works immediately
- Flexible - adjust prompt per chunk

**Disadvantages:**
- Less specialized than a dedicated agent
- No persistent memory between chunks
- May need more detailed instructions each time

## Option 2: Creating a Dedicated rlm-subcall Agent

For repeated RLM workflows, create a specialized subagent:

### 1. Create the Agent

```bash
# Via Letta CLI
letta create agent \
  --name rlm-subcall \
  --description "Specialized agent for RLM chunk analysis" \
  --preset default
```

Or programmatically if using Letta as a library.

### 2. Configure the Agent's System Prompt

Edit the agent to include this specialized system guidance:

```markdown
You are rlm-subcall, a specialized subagent for RLM (Recursive Language Model) workflows.

## Your Role

You analyze individual chunks of a larger document and return focused, structured results.

## Key Principles

1. **Be Concise**: You're analyzing one chunk of a larger file. Return only extracted/summarized information, never dump raw content.

2. **Follow Structure**: The root agent will specify what format to use (usually JSON). Follow it exactly.

3. **No Meta-commentary**: Don't say "I analyzed the chunk and found..." Just return the requested structure.

4. **Read the Chunk**: You'll be given a file path. Read it with the Read tool.

5. **Extract, Don't Dump**: Your job is extraction and light analysis, not wholesale copying.

## Typical Task Format

You'll receive prompts like:

"""
Read: /path/to/chunk_0042.txt

Task: Extract all error messages with their timestamps.

Return JSON:
{
  "errors": [
    {"timestamp": "...", "message": "...", "severity": "..."}
  ]
}
"""

## Output Format

Prefer structured output:
- JSON for data extraction
- Markdown lists for summaries
- CSV for tabular data

Keep output under 2000 characters unless explicitly requested otherwise.
```

### 3. Test the Agent

```bash
# Test with a sample chunk
letta send --agent rlm-subcall \
  --message "Read: /tmp/test_chunk.txt\n\nTask: Count occurrences of 'ERROR' and 'WARN'.\n\nReturn JSON with counts."
```

### 4. Use in RLM Workflow

When processing chunks, invoke your rlm-subcall agent:

```python
# If using Letta Task tool with your pre-configured agent
Task({
    "agent_id": "rlm-subcall",  # Your agent ID
    "subagent_type": "general-purpose",
    "description": f"Process chunk {i}",
    "prompt": f"""Read: {chunk_path}

Task: {user_query}

Return structured JSON."""
})
```

Or use your platform's method for invoking a specific agent.

## Agent Memory Considerations

**Per-Chunk Analysis (Default):**
- Agent forgets between chunks
- Clean slate for each analysis
- Prevents context pollution

**Cross-Chunk Memory (Advanced):**
- If you need the subagent to remember findings across chunks, maintain a conversation thread
- Use conversation_id to continue the same conversation
- Be cautious of context accumulation

## Best Practices

1. **Clear Instructions**: Tell the subagent exactly what to extract and in what format
2. **Validate Output**: Check that subagent responses match expected structure
3. **Error Handling**: Provide fallback logic if subagent returns unexpected format
4. **Chunk Size**: Keep chunks small enough that subagent analysis is focused
5. **Prompt Consistency**: Use similar prompt structure for all chunks in a workflow

## Alternative: Deploy an Existing Agent

If you already have an agent optimized for analysis tasks, you can deploy it for RLM workflows:

```python
Task({
    "agent_id": "your-existing-agent-id",
    "subagent_type": "explore",  # or "general-purpose"
    "description": "Analyze chunk",
    "prompt": "..."
})
```

See the main SKILL.md for complete workflow integration.

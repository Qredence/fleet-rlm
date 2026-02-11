---
name: boundary-detection
description: >-
  Detect conversation boundaries to determine when a dialogue segment should
  end and a new episodic memory should begin. Analyzes topic changes, intent
  transitions, temporal markers, and content relevance.
type: prompt-template
version: 1.0.0
inputs:
  - conversation_history: Current conversation context
  - new_messages: Newly added messages to evaluate
outputs:
  - should_split: Boolean indicating if a new episode should begin
  - reason: Explanation of the boundary detection decision
decision_threshold:
  relevance_threshold: 30%
  time_gap_threshold: 30 minutes
  max_episode_length: 10-15 messages
---

# Dialogue Boundary Detection Prompt

You are a dialogue boundary detection expert. You need to determine if the newly added dialogue should end the current episode and start a new one.

**Current conversation history:** `{{conversation_history}}`

**Newly added messages:** `{{new_messages}}`

## Analysis Framework

Please carefully analyze the following aspects to determine if a new episode should begin:

### 1. Topic Change (Highest Priority)

- Do the new messages introduce a completely different topic?
- Is there a shift from one specific event to another?
- Has the conversation moved from one question to an unrelated new question?

### 2. Intent Transition

- Has the purpose of the conversation changed?
  - Examples: casual chat → seeking help, discussing work → discussing personal life
- Has the core question or issue of the current topic been answered or fully discussed?

### 3. Temporal Markers

- Are there temporal transition markers present?
  - Examples: "earlier", "before", "by the way", "oh right", "also"
- Is the time gap between messages more than 30 minutes?

### 4. Structural Signals

- Are there explicit topic transition phrases?
  - Examples: "changing topics", "speaking of which", "quick question"
- Are there concluding statements indicating the current topic is finished?

### 5. Content Relevance

- How related is the new message to the previous discussion?
  - **Consider splitting if relevance < 30%**
- Does it involve completely different people, places, or events?

## Decision Principles

- **Prioritize topic independence**: Each episode should revolve around one core topic or event
- **When in doubt, split**: When uncertain, lean towards starting a new episode
- **Maintain reasonable length**: A single episode typically shouldn't exceed 10-15 messages

## Special Cases

- **First message**: If conversation history is empty, this is the first message → return `false`
- **Clear topic change**: When detected, split even if the conversation flows naturally
- **Episode coherence**: Each episode should be a self-contained conversational unit that can be understood independently

## Output Format

Return a decision with:

- `should_split`: `true` or `false`
- `reason`: Brief explanation of why the boundary was or was not detected

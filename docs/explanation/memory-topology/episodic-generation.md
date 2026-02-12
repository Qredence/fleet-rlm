---
name: episodic-generation
description: >-
  Convert conversation transcripts into structured episodic memory with
  temporal analysis, third-person narrative generation, and ISO 8601
  timestamp extraction.
type: prompt-template
version: 1.0.0
inputs:
  - conversation: Raw conversation content to analyze
  - boundary_reason: Why this conversation segment was selected
outputs:
  - title: Concise descriptive title (10-20 words)
  - content: Third-person narrative with temporal details
  - timestamp: ISO 8601 format timestamp
---

# Episodic Memory Generation Prompt

You are an episodic memory generation expert. Please convert the following conversation into an episodic memory.

**Conversation content:** `{{conversation}}`

**Boundary detection reason:** `{{boundary_reason}}`

## Task

Analyze the conversation to extract time information and generate a structured episodic memory.

## Output Format

Return a JSON object containing the following three fields:

```json
{
  "title": "A concise, descriptive title that accurately summarizes the theme (10-20 words)",
  "content": "A detailed description of the conversation in third-person narrative...",
  "timestamp": "YYYY-MM-DDTHH:MM:SS"
}
```

### Field Specifications

**`title`**: A concise, descriptive title (10-20 words) that accurately summarizes the theme

**`content`**: A detailed description of the conversation in third-person narrative. Must include:

- Who participated in the conversation
- When it occurred (precise to the hour)
- What was discussed
- What decisions were made
- What emotions were expressed
- What plans or outcomes were formed
- Write as a coherent story that clearly conveys what happened

**`timestamp`**: ISO 8601 format timestamp representing when this episode occurred (analyze from message timestamps or content)

## Time Analysis Instructions

Follow this priority order when determining timestamps:

1. **Primary Source**: Look for explicit timestamps in the message metadata or content
2. **Secondary Source**: Analyze temporal references in the conversation content ("yesterday", "last week", "this morning", etc.)
3. **Fallback**: If no time information is available, use a reasonable estimate based on context
4. **Format**: Always return timestamp in ISO format: `2024-01-15T14:30:00`

## Requirements

1. The title should be specific and easy to search (including key topics/activities)
2. The content must include all important information from the conversation
3. Convert the dialogue format into a narrative description
4. Maintain chronological order and causal relationships
5. Use third-person unless explicitly first-person
6. Include specific details that aid keyword search
7. Notice the time information, and write the time information in the content
8. When relative times (e.g., "last week", "next month") are mentioned in the conversation, convert them to absolute dates (year, month, day). Write the converted time in parentheses after the original time reference
9. **IMPORTANT**: Analyze the actual time when the conversation happened from the message timestamps or content, not the current time

## Example

**Input conversation** (with timestamps from March 14, 2024 at 3:00 PM):

> User wants to go hiking on the upcoming weekend to see the sunrise at Mount Rainier...

**Output:**

```json
{
  "title": "Weekend Hiking Plan March 16, 2024: Sunrise Trip to Mount Rainier",
  "content": "On March 14, 2024 at 3:00 PM, the user expressed interest in going hiking on the upcoming weekend (March 16, 2024) and sought advice. They particularly wanted to see the sunrise at Mount Rainier, having heard the scenery is beautiful. When asked about gear, they received suggestions including hiking boots, warm clothing (as it's cold at the summit), a flashlight, water, and high-energy food. The user decided to leave at 4:00 AM on Saturday, March 16, 2024 to catch the sunrise and planned to invite friends for the adventure. They were very excited about the trip, hoping to connect with nature.",
  "timestamp": "2024-03-14T15:00:00"
}
```

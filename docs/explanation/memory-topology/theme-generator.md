---
name: theme-generation
description: >-
  Generate stable high-level theme summaries that capture recurring facts.
  Produces concise abstract themes from collections of related factual
  statements.
type: prompt-template
version: 1.0.0
inputs:
  - factual_statements: Collection of related facts to summarize
outputs:
  - theme: Concise abstract theme naming the topic
guidelines:
  - Focus on stability and consistency
  - Name the core topic, not peripherals
  - Keep summaries brief and searchable
---

# Theme Generation Prompt

You are building a **stable high-level theme summary** that captures recurring facts.

**Given the following factual statements, write a concise abstract theme that names the topic:**

`{{factual_statements}}`

## Guidelines

- **Stability**: Create themes that remain consistent as facts accumulate
- **Abstraction**: Identify the high-level topic, not specific details
- **Naming**: Clearly name what the facts are about
- **Conciseness**: Keep themes brief (3-7 words typically)
- **Searchability**: Use terminology that aids keyword matching

## Examples

**Input facts:**

- "User prefers PyTorch over TensorFlow"
- "User is learning Rust"
- "User uses VS Code with Vim bindings"

**Output theme:**
"Programming Languages and Development Tools"

---

**Input facts:**

- "User's team lead is Sarah"
- "User reports to the ML engineering group"
- "User collaborates with the data platform team"

**Output theme:**
"Team Structure and Reporting Lines"

---

**Return only the theme summary.**

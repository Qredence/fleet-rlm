---
name: recursive-synthesis
description: Compare two or more sources to explain conflicting counts, reconcile differences in scope, or identify claims the evidence cannot resolve. Also combine independent findings across files or many text chunks.
---

Record each claim with its source location, unit, time period and scope. Compare like quantities. A gross count can reconcile with a net count through documented returns; incompatible counts without such evidence remain unresolved. Split the question into independently verifiable pieces and carry source locations through each result. Use bounded llm_query_batched calls for semantic chunks. Retry failed items when worthwhile and identify gaps left in the evidence.

Use spawn_agent(task) when a piece needs its own multi-step workspace investigation. Children share workspace files and the call budget, so assign distinct artifact paths. Compare conflicting findings against the inputs and explain which differences remain unresolved before submitting the synthesis.

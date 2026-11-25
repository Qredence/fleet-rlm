---
name: recursive-synthesis
description: Combine independent findings across reports, files, or many text chunks to answer a shared question or reconcile conflicting evidence.
---

Split the question into independently verifiable pieces and carry source locations through each result. Use bounded llm_query_batched calls for semantic chunks. Retry failed items when worthwhile and identify gaps left in the evidence.

Use spawn_agent(task) when a piece needs its own multi-step workspace investigation. Children share workspace files and the call budget, so assign distinct artifact paths. Compare conflicting findings against the inputs and explain which differences remain unresolved before submitting the synthesis.

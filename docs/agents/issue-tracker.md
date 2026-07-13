# Issue tracker: Local Markdown

Issues, PRDs, and Wayfinder maps for this repository live as local Markdown under `.scratch/`.
The directory is intentionally gitignored: it is a coordination surface for agents sharing this checkout, not a second canonical roadmap.

## Conventions

- One effort per directory: `.scratch/<effort>/`.
- A PRD, when needed, is `.scratch/<effort>/PRD.md`.
- Ordinary implementation issues are `.scratch/<effort>/issues/<NN>-<slug>.md`, numbered from `01`.
- Triage state is a `Status:` line near the top of each issue.
- Comments append under `## Comments`.
- Settled architecture and workflow decisions are published into the owning
  current documentation page linked from `docs/index.md`; superseded plans
  remain available through Git history.

## Wayfinding operations

- **Map:** `.scratch/<effort>/map.md` contains Destination, Notes, Decisions so far, Not yet specified, and Out of scope.
- **Child ticket:** `.scratch/<effort>/issues/<NN>-<slug>.md` contains `Type:`, `Status:`, `Blocked by:`, and one `## Question`.
- **Blocking:** a comma-separated `Blocked by:` list names ticket numbers. A ticket is unblocked when every listed ticket is `resolved`.
- **Frontier:** open, unblocked tickets ordered by ticket number.
- **Claim:** change `Status: open` to `Status: claimed` before doing any work.
- **Resolve:** append the result under `## Answer`, change the status to `resolved`, then add one linked gist to the map's Decisions so far.
- **Session limit:** resolve at most one Wayfinder ticket per session.

## Publishing and fetching

When a skill says to publish to the tracker, create the appropriate file under `.scratch/<effort>/`.
When a skill says to fetch a ticket, read the referenced path or numbered issue file directly.

# ADR 0003: pi-tui with native terminal scrollback

Status: Accepted

## Context

Fleet must display every sanitized live and durable execution event in chronological order while preserving multiline editing, Unicode/IME input, bracketed paste, completion, and deterministic terminal cleanup. The Ink viewport and collapsible-card model made the application responsible for transcript scrolling and focus state.

## Decision

`@earendil-works/pi-tui@0.80.10` is Fleet's only terminal renderer. This ADR supersedes ADR 0002 only where it selects Ink and an application-managed viewport.

The main screen is one flat render history: transcript, activity, editor, and footer. Messages are fully expanded and never truncated by Fleet. pi-tui performs differential synchronized output and owns the multiline editor, completion, overlays, Unicode width, IME cursor, and bracketed-paste behavior.

Fleet does not enable mouse reporting, maintain transcript scroll state, pin the prompt, or clip old evidence. Wheel and trackpad scrolling use native terminal scrollback. Resize, hydration, clearing, and structural redraw may replay the transcript and return the terminal to the live bottom.

The FastAPI SSE transport, strict chunk validation, live/durable projection, atomic hydration, cancellation protocol, artifact download path, and backend contracts are unchanged.

## Consequences

- Node 22.19 or newer is required; pi-tui is pinned exactly while its API is pre-1.0.
- macOS and Linux are the supported cutover platforms. Windows remains deferred.
- Terminal scrollback capacity determines how far a user can navigate historically, although Fleet retains all messages in process memory.
- Help, Session, confirmation, and transactional Skill selection use overlays; the editor regains focus when an overlay closes.
- Static expanded evidence replaces card selection, collapsing, and keyboard thread navigation.

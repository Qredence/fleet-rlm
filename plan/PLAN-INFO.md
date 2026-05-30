Yes, latency will change—but not in a way that harms the user experience under normal use. Here’s a realistic breakdown.

---

## 1. Within an active session (sandbox already running)

**Simple reply (“What’s the weather today?”)**
- The backend sends the message to the sandbox (via an agent server or process execute).
- The agent uses a lightweight DSPy `ChainOfThought` → one LLM call.
- **Latency:** essentially the same as a direct LLM call + a few hundred milliseconds of sandbox communication overhead.
- **Impact:** negligible.

**Complex task (RLM with tools, child RLMs)**
- Already ran inside a sandbox in the old architecture, so no change.
- **Latency:** unchanged (still minutes for large tasks).

**Conclusion:** As long as the sandbox is alive, simple interactions feel just as snappy as before.

---

## 2. First message after a long pause (sandbox auto‑stopped)

- Daytona auto‑stops the sandbox after 15 minutes of inactivity (configurable).
- On the next message, the backend must **create a new sandbox** with the same volume.
- Sandbox creation typically takes **10–30 seconds** (image pull, volume mount, agent startup).
- The frontend would show a “starting your session…” indicator while the sandbox spins up.
- Once the sandbox is ready, subsequent messages are fast again.

**This is the only real latency penalty** compared to the old stateless model where simple chats didn’t use a sandbox at all.

### Mitigations
- **Increase `auto_stop_interval`** to 30–60 minutes to reduce how often this happens.
- **Keep‑alive pings** from the frontend (e.g., every 10 minutes) to prevent auto‑stop during long reading sessions.
- **Pre‑warmed sandboxes** – not in the plan, but a future optimization could reuse an existing stopped sandbox by restarting it (faster than full creation; Daytona supports this? Possibly, but not guaranteed). For now, we accept the cold start.

---

## 3. Child RLM sandboxes

- Each `delegate_to_rlm` spawns an ephemeral sandbox.
- These already existed in the old architecture and are unchanged.
- Their creation latency (a few seconds) is hidden because the parent agent continues working and aggregates results later.

---

## 4. Overall latency comparison

| Scenario | Old `fleet‑rlm` | New `fleet‑rlm` | Difference |
|----------|----------------|-----------------|------------|
| Simple chat, active session | Direct LLM call (~1–3s) | Sandbox agent call (~1–4s) | +0–1s |
| Simple chat after auto‑stop | Direct LLM call (~1–3s) | Sandbox creation + agent call (~15–35s) | **+15–30s** (cold start) |
| Complex RLM task | Sandbox run (minutes) | Same sandbox run (minutes) | No change |
| Reconnect (sandbox alive) | Not supported | Instant (just forward message) | N/A |
| Reconnect (sandbox stopped) | Not supported | New sandbox creation (~15–30s) | N/A (new feature) |

The **only** scenario where latency worsens noticeably is the first message after a long idle period. In exchange, you get full statefulness, session continuity, and elimination of the memory‑gap/context‑bloat problems.

---

## 5. Is it worth the trade‑off?

**Yes.** Most user sessions are active within a few minutes of each other. The cold‑start delay only occurs after extended breaks, and even then, the user sees a “starting” indicator—it’s not a mysterious hang. The benefits of persistent memory, knowledge, and a unified agent far outweigh this occasional 15–30 second pause.
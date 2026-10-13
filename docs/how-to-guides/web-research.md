# Public web research

Fleet's bundled `web-research` Skill discovers public sources and reads or
navigates pages inside the Root Daytona Sandbox. DSPy owns the reasoning loop;
agent-browser executes browser actions without a second model or planner.

## Search configuration

The optional Tavily Search API uses a host-only key resolved from the environment
named by `web.api_key_env` in `config/fleet.toml` (shipped value: `TAVILY_API_KEY`).
Set that secret in the host environment or repository `.env`, then restart Fleet.
It is never injected into the Sandbox. The `web.search_enabled` setting controls
whether a configured client is bound; without a key, supplied public URLs can
still be read locally.

The `web_search` tool accepts a public `query`, `max_results` from 1 to 10
(default 5), and `depth` of `basic` or `advanced`. It returns URLs and bounded,
unverified snippets; it does not produce an answer. Only public search queries
are sent to Tavily. Do not send private source bodies or Session contents.

`web.search_credits_per_turn` defaults to 62. Basic requests reserve one Tavily
credit, advanced requests two, before dispatch. Reservations remain consumed on
failures because disconnected requests may still have reached the provider.
There are no implicit retries or fallback providers. At the current listed
pay-as-you-go rate of $0.008 per credit, 62 credits correspond to $0.496 of web
API usage per Turn; this is independent of model billing. Consult
[Tavily pricing](https://docs.tavily.com/documentation/api-credits) before changing
operator spending assumptions.

## Skill and evidence

With default progressive discovery, load `web-research` when a task requires
public source discovery or page access. Exact Skill selections restrict loading:
select `web-research` and `long-context` together when the task needs both web
access and long-document analysis. Skills are guidance, not tool authorization.

Version 1.1.0 removes the model-readable browser launcher and command reference.
Reselect the new version if an earlier Turn pinned 1.0.0. Loading the Skill
projects its HTTP helper into
`/workspace/skills/web-research`. A failed resource installation does not unload
the Skill. Root receives successful paths, installation counts, and safe failure
codes for both explicit preloads and progressive loads. If a helper is missing,
use `read_skill_resource` and save its content in Run scratch without reloading
the Skill.

Version 1.2.0 adds bounded JSON source capture and optional primary-source domain
restrictions. Call `web_search(..., include_domains=["daytona.io", "github.com"])`
when the request names those publishers. Fleet validates up to ten domain names,
requests Tavily's restrictive domain mode, and excludes out-of-scope result URLs
even if the provider returns them. Omit the filter for general source discovery.
The filter does not change the credit reservation or retry policy.

Version 1.2.1 clarifies operational recommendations after uncertain execution:
an isolated interpreter namespace does not undo or deduplicate external tool
effects. Replay advice must establish idempotency or reconcile authoritative
operation state rather than infer safety from a disconnected output stream.

The HTTP helper verifies redirects, response size, and declared transfer length,
then saves all available extracted text and provenance under `FLEET_RUN_SCRATCH`.
Oversized or incomplete evidence fails instead of silently saving a prefix.
HTML is extracted as readable text; plain text and JSON documents are retained
without HTML interpretation. Parse saved JSON locally when inspecting package
metadata. Binary archives are not page evidence and require a separate bounded,
checksum-verified download.
Use bounded excerpts for analysis. For browser pages, record the actual final
URL and title and save rendered text with the helper's `save_evidence` function.
Accessibility snapshots identify controls; they are not complete page captures.
Coverage does not include unread pagination, hidden sections, frames, or later
dynamic updates. Copy selected verified evidence into Session Workspace or
publish an Artifact when it should survive the Turn.

Execution output capture is limited to 65,536 characters per stdout/stderr
stream before SDK transfer. Excess text is discarded with a truncation notice;
execution continues and a valid final submission remains available. Full evidence
belongs in files, not printed output. Unexpected transport excess instead fails
the invocation and fences root reuse; it does not trigger an execution retry.

## Browser environment and cleanup

The new candidate Session image is `fleet-rlm-python313-v12`. It contains pinned
agent-browser 0.39.0, matching upstream core Skill resources, and the already
pinned Playwright/Chromium installation. The native Linux x64 binary and source
archive are checksum-verified during image construction; Node is not added as a
runtime dependency. Published `v11` remains the earlier Playwright-only image.

Browser usage comes from the installed, matching `agent-browser skills get core`
guide. Save it in Run scratch and inspect its headings and relevant sections in
bounded slices; its full body exceeds ordinary RLM observation limits. Fleet
keeps only the small invocation/evidence adapter in the Skill. Missing
`open_browser` means interactive browsing is unavailable, not permission to
import a launcher, install a browser, or use another session.

The native launcher source belongs to Daytona ownership, not Skill resources.
The `open_browser` tool starts `/opt/fleet/browser_session.py` once and retains
the launch process group on the host. The Skill uses its returned CLI prefix
and environment for every action. Fleet launches non-root headless Chromium with its
internal sandbox enabled, a fresh profile, and loopback CDP. agent-browser
attaches to this browser; its stock container launcher is not used. Profiles,
browser sessions, and process markers are Run-scoped. Browser JSON output is
saved to files and previewed explicitly: agent-browser 0.39.0 does not apply
`--max-output` in JSON mode.

Normal driver disconnection and native pre-commit cleanup stop Run-owned
browser and driver processes. Host-retained process identity also covers orphaned
renderers after Chrome's leader crashes. Unconfirmed cleanup prevents success and taints
the retained Sandbox for retirement. A subprocess timeout alone does not prove
that a detached driver daemon has stopped. Browser network transfers are not
bounded by the extracted-text limit, and URL checks do not certify network
containment. See the accepted boundary in [architecture](../../ARCHITECTURE.md).

Create and verify the new image through the
[snapshot workflow](daytona-snapshot.md) before changing deployment references.
`verify-runtime` includes a local interactive browser fixture and confirmed
cleanup. Credentialed provider, Daytona, and comparative evaluation runs require
explicit operator authorization; local unit tests do not establish provider
compatibility or comparative search quality.

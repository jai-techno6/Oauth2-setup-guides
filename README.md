# OAuth App Setup Guides

Before an AI agent's users can click "Connect Gmail", the developer may have to register an OAuth app with the
provider and get it approved. This tool tells them up front how hard that is: the difficulty tier, hands-on time,
approval wait, requirements, what can block them, and which steps an agent can do versus where they take over.
Every claim is labelled **tested**, **cited**, **inferred**, or **unknown**.

It has two parts:

- **A curated library** of six guides chosen to cover each difficulty tier: GitHub (trivial), HubSpot (moderate),
  Slack and Google/Gmail (hard), LinkedIn and X (gated). Each one was spot-checked against provider docs on 2026-10-28.
- **Live generation.** Type any app name or website. A research agent investigates it while you watch and
  produces a guide with a shareable permalink, plus Markdown and JSON exports.

## Run it locally

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # Windows: copy .env.example .env
# edit .env and set OPENAI_API_KEY
python -m pytest            # offline tests; no API key or internet needed
python -m app               # http://127.0.0.1:8000
```

Without `OPENAI_API_KEY`, the curated library still works and the generate box explains that live generation is off.

To try the live agent from the command line (this checks your key and model before you deploy):

```bash
python scripts/try_generate.py "Notion"
```

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | (required for live generation) | Research and writing |
| `OPENAI_MODEL` | `gpt-5.4-mini` | Any Responses-API model that supports the `web_search` tool |
| `OPENAI_REASONING_EFFORT` | `low` | `low`/`medium`/`high`. If the model rejects it, the app retries without it |
| `COMPOSIO_API_KEY` | empty | Optional. Checks Composio's live toolkit catalog for managed OAuth (marked **tested**) |
| `ACCESS_CODE` | empty | If set, live generation requires this code. **Set it on any public URL** to protect your OpenAI bill |
| `GENERATE_RATE_PER_HOUR` | `10` | Live generations per client IP per hour |
| `MAX_CONCURRENT_JOBS` | `3` | Agent runs in parallel; extra jobs queue |
| `DATA_DIR` | `./var` | Where the SQLite file of generated guides lives |

## Deploy on Railway

1. Push this folder to a GitHub repo and create a new Railway service from it (**New Project > Deploy from
   GitHub repo**). Railway detects Python from `requirements.txt` and `.python-version`.
2. `railway.json` sets the start command (`python -m app`), the healthcheck (`/healthz`) and the restart policy,
   so no manual settings are needed. The app reads Railway's `PORT`.
3. Under **Variables**, add `OPENAI_API_KEY`, `ACCESS_CODE`, and optionally `COMPOSIO_API_KEY`.
4. **Persist generated guides:** add a Volume mounted at `/data` and set `DATA_DIR=/data`. Without a volume,
   generated guides disappear on each redeploy. Curated guides live in the repo and are never lost.
5. Under **Networking**, generate a domain. Open `/healthz`; it should report `"curated": 6, "load_errors": 0`.

## Using it

- `/` is the library and the generate box. Entering an app that already has a guide opens the existing one
  (curated first, then the newest generated guide). Agent guides have a "Regenerate" button.
- `/guides/<slug>` is the shareable guide. It has a checklist (ticks are saved in your browser), "Copy link",
  "Copy as Markdown" (for pasting into Slack, Notion or a PR), JSON, and print.
- `/jobs/<id>` shows live progress. You see each research pass, the search queries, the sources found, each
  probe result and the quote-check counts.
- `/audit` shows what was checked in the curated guides, and what was corrected.
- `/about` explains the method and the evidence labels.
- `/api/docs` is the OpenAPI documentation.

## Key decisions

**Scope: approval-gated providers and the "where you take over" boundary.** Every provider handles user
connections and token refresh (layers 2 and 3) the same way. The provider OAuth app (layer 1) is where time
estimates swing from five minutes to months. The guides therefore focus on what blocks production use by
*external* users (verification, listing review, install caps, legal entity requirements, paid tiers) rather
than on OAuth mechanics. Each guide also answers "do I need my own app at all?" using Composio's managed-app
status, because for prototyping the honest answer is often "not yet".

**The difficulty tier is set by the hardest step required for external users, not by the signup form.** This
changed results. Slack is usually called "moderate". Slack's 2025 terms limit unlisted commercial apps to
1 request/minute on message history, and a Marketplace listing needs 10 active workspaces plus up to about
10 weeks of review. For an agent that reads messages, that makes Slack hard.

**Evidence labels are enforced in code, not by the model's word.**
- The model may only output `cited`, `inferred` or `unknown`. Only checks the code runs produce `tested`.
- A `cited` claim with no valid source is downgraded to `inferred`.
- The agent fetches every cited page and searches it for the quote. A quote that cannot be found on a readable
  page downgrades the claim to `inferred`. If the page is blocked or JavaScript-only, the claim is flagged
  "not machine-checked" and keeps its label.
- Sources are tagged official, Composio or third-party, so a price that only a blog states shows up that way.

**The agent attempts as much setup as it can without credentials, and records where it is stopped.** Instead of
a full browser agent, the probe step uses HTTP: it fetches OAuth/OpenID discovery documents, checks that the
authorization endpoint is live, and opens the developer console. A login redirect or a bot block (HTTP 403) is
recorded as **tested** evidence for the "you take over" section. The agent never logs in, signs terms, pays or
solves CAPTCHAs. Those are exactly the steps the guide hands to the developer.

**A fixed schema over free text.** Every guide has the same sections (tier, time split into hands-on and
approval wait, fastest path, phases with agent/human steps, requirements, gotchas, blockers with workarounds,
conflicts, unknowns, sources, probes). That makes guides comparable, renderable, exportable and testable. The
model fills it through OpenAI structured outputs in strict mode.

**Three narrow research passes in parallel** (registration, approval and distribution, paid tiers and recent
changes) instead of one broad prompt. Narrow questions find review timelines and install caps more reliably,
running in parallel keeps the live demo to roughly 1-3 minutes, and each pass streams progress.

**Edge cases.** For an app with no third-party OAuth, the guide gets a `no_oauth` tier that names the auth method the
provider does offer. Conflicting sources go into a "Where sources disagree" list instead of being resolved silently
(the Gmail CASA cost ranges from about $540 to $50k+ depending on the source). Paid access is a `gated` tier with
cost guidance (X). Unknown or ambiguous apps get the `unknown` tier and low confidence.

## Evidence audit (summary)

A full table is at `/audit`. Highlights:

| Claim (common belief) | Result | Basis |
|---|---|---|
| Slack is moderate | **Corrected: hard** for agents that read messages | Slack changelog (2025-05-29) and Marketplace review guide |
| gmail.compose is a sensitive scope | **Corrected: restricted** | Gmail scopes page |
| GitHub OAuth tokens never expire | **Corrected:** expiring tokens are on by default (8 h / 6 months) | GitHub docs |
| GitHub callbacks must match exactly | **Corrected:** wildcard matching is per callback; apps created before 2026-08-03 have it on | GitHub docs |
| LinkedIn gives refresh tokens | **Corrected:** only to approved MDP partners; others get 60-day tokens | LinkedIn docs |
| GitHub wildcard cutoff date (Aug 3, 2026) | **Re-verified** independently against GitHub's callback-URL docs | GitHub docs |
| Slack review: 10 business days + up to 10 weeks, 10 active workspaces | **Re-verified** independently | Slack Marketplace review guide |
| X needs a Basic/Pro subscription | **Corrected:** now pay-per-usage credits | X docs snippet and third-party pricing posts |
| HubSpot apps are created in the web UI | **Corrected:** CLI/project platform; 10/25 install caps before listing | HubSpot changelog and docs |
| Gmail needs verification plus CASA | **Verified** for restricted scopes processed on your servers | Google restricted-scope page |
| Composio redirect URI | **Conflict:** Composio's own guide shows both v1 and v3 URLs | Guide tells you to copy it from your auth config |
| CASA cost | **Conflict:** $540/yr to $50k+ in third-party sources; Google publishes none | Left as a range |

**What "tested" means here.** These checks were run on 2026-10-28 without credentials: the Google and LinkedIn
OpenID discovery documents (endpoints confirmed) and the Google Cloud console (redirects to sign-in). GitHub,
Slack, LinkedIn and X consoles returned HTTP 403 to automated clients. HubSpot returned a JavaScript shell.

**What was not done.** No developer accounts were created and no OAuth app was registered or submitted for
review. Hands-on times are therefore labelled **inferred**. `docs/MANUAL_CHECKS.md` lists the checks to do by
hand to turn them into tested numbers.

## Limitations

- **No authenticated browser agent.** The probe uses plain HTTP and stops at the first login or bot wall. That is
  honest, but it learns less than a real walkthrough. A Playwright agent driving a logged-in console session is the
  natural next step.
- **Many official pages block automated fetches** (Slack, X, parts of GitHub). In those cases the quote check
  reports "not machine-checked" instead of verifying.
- **Quote checks match text, not meaning.** A real quote placed next to the wrong claim would pass. Spot-check
  anything you plan a launch around.
- **Time estimates are thin.** Providers rarely publish review times, and live guides often leave the wait as unknown.
- **Generated guides are not reviewed by a person.** The UI says so on every agent guide.
- **In-memory jobs.** Progress streams live in one process. A restart drops in-flight jobs (saved guides persist
  with a volume). Run one instance.
- **SSRF protection** blocks private and loopback addresses and non-standard ports on every redirect hop. It does
  not defend against DNS rebinding between the check and the connection.
- **Rate limiting is per IP and in memory.** Use `ACCESS_CODE` on public deployments.
- **Six curated guides, not 100.** The schema, rendering and pipeline would carry a larger library. Batch-generating
  the remaining guides and then human spot-checking them is straightforward follow-up work.

## Project layout

```
app/
  main.py            routes: pages, API, SSE progress stream
  models.py          guide schema and evidence labels
  store.py           curated JSON + SQLite for generated guides
  jobs.py            background jobs, replayable progress events, rate limiter
  render.py          Markdown export
  agent/
    pipeline.py      resolve -> research -> synthesize -> probe -> Composio -> verify
    research.py      three parallel web-search passes (OpenAI Responses API, web_search tool)
    synthesize.py    strict JSON schema output into the guide model
    probe.py         discovery docs, authorize endpoint, console login walls
    verify.py        evidence-rule enforcement and quote checks against cited pages
    netsafe.py       SSRF-safe fetching
    composio.py      optional live Composio catalog lookup
  data/guides/*.json curated guides
  templates/, static/
tests/               offline tests (fake LLM, mocked HTTP)
docs/MANUAL_CHECKS.md
```

No credentials are in the repo. `.env` is git-ignored.

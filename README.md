# OAuth App Setup Guides

**Live:** TODO-your-railway-url  ·  **Access code for live generation:** shared in the submission form
Created by - Jai Anmol Sharma  (IIT Kanpur)
Before an AI agent's users can click "Connect Gmail", the developer may have to register an OAuth app with the
provider and get it approved. Today they find out how hard that is by trial and error. This tool tells them up
front: how difficult it is, how long it takes including the approval wait, what can block them, and which steps
an agent can do versus where they have to take over. Every claim is labelled **tested**, **cited**,
**inferred** or **unknown**, so they know how far to trust it.

It has two parts:

- **A curated library** of six guides chosen to cover every difficulty tier: GitHub (trivial), HubSpot
  (moderate), Slack and Google/Gmail (hard), LinkedIn and X (gated).
- **Live generation.** Type any app name or website. A research agent investigates it while you watch and
  produces a guide with a shareable link, plus Markdown and JSON exports.

## The problem, as I understood it

Three layers are involved when an agent's user connects an app:

1. **The provider OAuth app**: a one-time registration with Google, Slack and so on. It gives a client ID and
   secret, a redirect URI, the permissions (scopes) and the consent screen users see.
2. **The developer's integration**: in Composio, an auth config that points at that OAuth app.
3. **User connections**: each end user approves the consent screen; Composio stores and refreshes their tokens.

Layers 2 and 3 work the same for every provider. Layer 1 does not: GitHub takes five minutes, Gmail can take
weeks plus a paid security assessment, and LinkedIn may simply refuse access. Composio's
[managed OAuth apps](https://docs.composio.dev/toolkits/managed-auth) let developers skip layer 1 while
prototyping. They come back to it when they need their own name on the consent screen, custom scopes, their
own rate limits, or an app Composio does not manage. That moment is what this tool is for.

## Key product decisions

**Focus on approval, not OAuth mechanics.** Creating an OAuth app is a form almost everywhere. What makes
timelines swing from minutes to months is what happens before *external* users can connect: verification,
marketplace review, install caps, business checks, paid tiers. The guides spend their space there.

**Two clocks, not one.** Every guide separates hands-on time (usually under an hour) from the wait for the
provider (none to several weeks), and gives the fastest way to start while waiting. Providers' docs blur these.

**Difficulty is set by the hardest step for real users, and depends on the permissions requested.** Gmail
send-only is a lighter review; reading mail triggers restricted-scope verification and an annual security
assessment. Slack is often called "moderate", but for an agent that reads messages it is hard: since 2025,
unlisted commercial apps get 1 request/minute on message history, and a Marketplace listing needs 10 active
workspaces plus up to ~10 weeks of review.

**Answer "do I need my own app at all?" first.** Each guide states whether Composio already provides a managed
OAuth app, because for prototyping the honest answer is often "not yet".

**Evidence labels are enforced in code, not trusted to the model.**
- Only checks the code runs can produce **tested**. The model may only say cited, inferred or unknown.
- A **cited** claim with no valid source is downgraded to inferred.
- The agent fetches each cited page and looks for the quoted sentence. If it is not there, the claim is
  downgraded. If the page cannot be read (bot protection, JavaScript-only), it is flagged "not machine-checked".
- Sources are tagged official, Composio or third-party, so a price only a blog states shows up that way.
- Conflicting sources are shown side by side instead of being resolved silently.

**The agent does what it can without credentials and records where it stops.** It reads OAuth discovery
documents, checks the authorization endpoint is live, and opens the developer console. Where it hits a sign-in
form or bot block, that becomes tested evidence for the "you take over" section. It never logs in, accepts terms,
pays or solves CAPTCHAs; those steps are exactly what the guide hands to the developer.

**A fixed guide format.** Every guide has the same sections (tier, time, steps marked agent or you,
requirements, gotchas, blockers with workarounds, unknowns, sources), so guides are comparable, shareable and
easy to check. The model fills it through structured output.

## What I checked, corrected, and could not verify

**Curated guides.** Spot-checked against provider docs on 2026-10-28. Full table at `/audit`. Highlights:

| Common belief | Result | Basis |
|---|---|---|
| Slack is moderate | **Corrected: hard** for agents that read messages | Slack changelog (2025-05-29), Marketplace review guide |
| Slack review: 10 business days + up to 10 weeks, 10 active workspaces | **Verified** (re-checked independently) | Slack Marketplace review guide |
| `gmail.compose` is a sensitive scope | **Corrected: restricted** | Gmail scopes page |
| GitHub OAuth tokens never expire | **Corrected:** short-lived tokens are on by default for new apps (8 h / 6 months) | GitHub docs and changelog |
| GitHub callbacks always match exactly | **Corrected:** wildcard matching is per callback; single-callback apps created before Aug 3, 2026 have it on | GitHub docs (re-checked) |
| LinkedIn gives refresh tokens | **Corrected:** only to approved partners; others re-authorise every 60 days | LinkedIn docs |
| X needs a Basic/Pro subscription | **Corrected:** pay-per-use credits since early 2026 | X docs + third-party pricing posts |
| Gmail needs verification + security assessment | **Verified** for restricted scopes | Google restricted-scope docs |
| Cost of Google's security assessment (CASA) | **Could not verify:** third-party figures range widely; Google publishes none | Left as a range, flagged |

**The live agent.** I compared a generated Zoom guide with Zoom's own pages:

| Agent's claim | Result |
|---|---|
| External sharing approval: Zoom replies within 3-4 business days | **Verified** |
| Needs SSDLC docs, SAST/DAST scan results and a privacy policy | **Verified but incomplete:** Zoom also requires any 3 of 5 further documents (pen-test summary, security policy, incident response, etc.). The agent missed this |
| Refresh tokens expire after 90 days | **Verified** (Zoom staff, developer forum) |
| Developer console "asks the visitor to sign in" (labelled tested) | **Wrong, fixed in code:** the agent had opened a docs page with a "Sign in" link in its header. The probe now requires a real sign-in form |

**Setup attempts by hand.** TODO: fill in from your own attempts, e.g.
- GitHub OAuth app: registered in __ minutes; short-lived tokens were / were not on by default.
- Google Cloud OAuth client: created in __ minutes; the console labelled `gmail.readonly` as Restricted.

**What I learned.** The quote check catches invented sources but not omissions: a guide can be accurate and
still miss a requirement, as the Zoom one did. Generated guides are a strong first draft, so every agent guide
says "not reviewed by a person". The useful metric for the agent is how often its tier and approval wait match
a human check; `docs/MANUAL_CHECKS.md` lists the checks.

## Run it locally

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # then set OPENAI_API_KEY
python -m pytest            # offline tests, no key needed
python -m app               # http://127.0.0.1:8000
```

Without `OPENAI_API_KEY` the curated library still works and live generation is switched off.

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | (required for live generation) | Research and writing |
| `OPENAI_MODEL` | `gpt-5.4-mini` | Any Responses-API model with the `web_search` tool |
| `OPENAI_REASONING_EFFORT` | `low` | `low` / `medium` / `high` |
| `COMPOSIO_API_KEY` | empty | Optional: checks Composio's live catalog for managed OAuth |
| `ACCESS_CODE` | empty | Required to run live generation. **Set it on any public URL** |
| `GENERATE_RATE_PER_HOUR` | `10` | Live generations per IP per hour |
| `DATA_DIR` | `./var` | Where generated guides are stored (SQLite) |

## Deploy on Railway

1. Push to GitHub, then in Railway choose **New Project → Deploy from GitHub repo**.
2. Start command `python -m app`, healthcheck `/healthz` (set by `railway.json` if present; otherwise set
   them under Settings → Deploy). The app reads Railway's `PORT`.
3. Variables: `OPENAI_API_KEY`, `ACCESS_CODE`, optionally `COMPOSIO_API_KEY`.
4. Add a Volume mounted at `/data` and set `DATA_DIR=/data`, or generated guides vanish on each redeploy.
5. Generate a domain and open `/healthz`; it should show `"curated": 6`.

## Using it

- `/`: the library and the generate box. An app that already has a guide opens it directly.
- `/guides/<slug>`: the shareable guide, with a checklist, copy link, copy as Markdown, JSON and print.
- `/jobs/<id>`: live progress: each search, source, probe and quote check as it happens.
- `/audit`: what was checked and corrected in the curated guides. `/about`: method and labels.

## Limitations

- **No logged-in browser agent.** The agent stops at the first sign-in page. A browser agent working inside the
  developer's own logged-in session is the natural next step: it could fill in the app form itself.
- **Many official docs block automated reading** (Slack, X, parts of GitHub), so some quotes are "not
  machine-checked".
- **Quote checks match text, not meaning**, and do not catch omissions. Check anything you plan a launch around.
- **Approval times are thin.** Providers rarely publish them; guides say "unknown" rather than guess.
- **Generated guides can repeat a source or an unknown** when parallel research passes overlap.
- **Six curated guides, not 100.** The format and pipeline would carry a larger library; batch-generating the
  rest and spot-checking a sample is the follow-up.
- **Single instance.** In-progress jobs and rate limits live in memory; a restart drops running jobs (saved
  guides persist with a volume).

## Project layout

```
app/
  main.py            pages, API, live progress stream
  models.py          guide format and evidence labels
  store.py           curated JSON + SQLite for generated guides
  agent/
    pipeline.py      research → write → probe → verify
    research.py      three parallel web-search passes
    synthesize.py    structured output into the guide format
    probe.py         discovery docs, authorize endpoint, console sign-in walls
    verify.py        evidence rules and quote checks
  data/guides/       the six curated guides
tests/               offline tests (fake model, mocked HTTP)
docs/MANUAL_CHECKS.md
```

No credentials are in the repo; `.env` is git-ignored.

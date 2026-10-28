# Manual checks (turn "inferred" into "tested")

The agent cannot log in, so some numbers in the curated guides are estimates. Each check below takes a few minutes
with a free account and replaces an estimate with a measured result. After a check, edit the guide JSON in
`app/data/guides/`:

- set `evidence` to `"tested"`
- put what you observed and the date in `note`
- add a row to that guide's `audit` list

`python -m pytest` confirms the file is still valid.

| Guide | Check | Replaces |
|---|---|---|
| GitHub | Time the registration of an OAuth app (Settings > Developer settings > OAuth apps > New). Confirm "Expire user access tokens" is checked by default. | `time.hands_on` (inferred 5-15 min), token-expiry gotcha |
| GitHub | Connect it in Composio with a custom auth config. Note the exact redirect URI the dashboard shows (v1 or v3). | Composio redirect URI conflict |
| Google (Gmail) | Create a project, Branding page and Web client. Add `gmail.readonly` under Data Access and confirm the console labels it Restricted. Note whether "Verify Branding" finishes in minutes. | `time.hands_on`, scope classification, brand verification time |
| Google (Gmail) | Authorize a test user in Testing mode. Check whether the refresh token fails after 7 days. | Testing-mode 7-day token claim (currently unverified) |
| Slack | Create an app from a manifest and activate public distribution. Time it. | `time.hands_on` (inferred 20-45 min), redirect URL steps |
| Slack | In the app's Marketplace submission page, read the review estimate shown. | `approval_wait` (cited max, not actual) |
| HubSpot | `npm i -g @hubspot/cli`, `hs project create --project-base app --auth oauth --distribution private`. Upload it and time the run. Check the authorize/token URLs in the app's Auth tab. | `time.hands_on`, endpoints (inferred), legacy-UI conflict |
| LinkedIn | Create an app. Note whether a company Page is mandatory at creation and which products appear as self-serve. | Page requirement note, self-serve products |
| X | Create an app and enable OAuth 2.0. Read the current per-request prices in the console. | Prices (third-party only), approval_wait |

Live generation: for a few apps, compare the agent guide with the provider's docs. Record whether the tier and
the approval wait were right, and keep a list. That error rate is the most useful number to report on the agent.

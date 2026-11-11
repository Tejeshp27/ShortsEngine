# Private control center

The browser now covers the local C.1–C.5 workflow. It uses FastAPI, Jinja2,
SQLite and ordinary Python processes. The theme uses warm neutral surfaces,
charcoal text and a muted olive accent. No fonts, charts or scripts load from
third-party CDNs.

## Run it

```bash
source .venv/bin/activate
pip install -r requirements.txt
python -m src.dashboard.app
```

Open http://localhost:8000 and use your existing login. Restart a dashboard
process that was running before the upgrade. Database changes are additive and
run on startup; no existing videos, passwords or review notes are removed.

Back up the database, output directory and encryption secret before moving to
another machine. `DATABASE_PATH` selects the database; Docker uses `/app/data/`.

## Pages

| Page | What it does |
| --- | --- |
| Overview | Totals across channels, channel cards, cumulative follower/view charts and recent activity |
| Channels | Each channel's connected accounts, daily followers, recent videos and best performers |
| Video library | Search titles, filter channels/status, play private MP4s, see cached thumbnails and durations |
| Review queue | Read sources and scripts, watch renders, approve or reject content |
| Money | Record USD payouts and expenses; see monthly totals, profit and the cost ledger |
| New video | Find recent sources, optionally rank them, use an article URL, create a script or animated video |
| Activity | Persistent progress, outcomes, saved drafts and errors for background jobs |

A dash means no statistics have been collected. A numeric zero means a real
observation reported zero. Overview publication counts use **confirmed public
post IDs**. Legacy `posted` statuses can include TikTok inbox deliveries; those
are not automatically counted as public posts.

Files under `output/videos/` that are missing from the database appear as **Not
in database**. Their source, cost and review status remain unknown. The library
does not automatically approve or publish them. Video and thumbnail URLs require
login, including requests for part of a video while seeking. Files and symlinks
outside `output/` cannot be served through these routes.

## Create a video

1. Open **New video**, choose a channel, and find recent stories. A topic filters
   the channel's current feed results; it does not create a source-free script.
   Optional AI ranking shows its spending allowance before submission.
2. Choose a story or paste a public article URL. The worker checks that meaningful
   source text is available before asking the writing model for a script.
3. Choose **Script for review** or **Script + animated video**. Free Edge speech
   is the default for browser jobs. The channel's configured voice is also an
   option; the news channel currently uses ElevenLabs.
4. A **dry run** checks the source but skips paid writing, voice generation and
   rendering. It does not create a pretend video in the review queue.
5. Watch Activity for progress. A successful script or video stays **drafted**.
   If rendering fails after writing, the saved draft remains available.
6. Review the script, source and on-screen claims. A script can also be rendered
   later from its detail page. Rendering returns the result to the review queue.

The example Twitch channel is disabled until its sources and permissions are
configured. Browser source generation currently supports article-based channels;
it does not implement a Twitch clip editor.

No browser job publishes content. Posting can still be done manually in the
platform apps. On a video's detail page, record its actual numeric public post ID
after posting; the account collector can then query that video's statistics.
A TikTok `publish_id` from inbox upload is a different identifier.

## Spending and worker behavior

`config/dashboard.yaml` contains the daily dollar limit, number of jobs allowed
in progress, job timeout and heartbeat timeout. Defaults are **$2 per UTC day**,
**one job in progress**, and **60 minutes per job**.

Each paid job reserves a conservative allowance calculated from the configured
models, token ceilings and optional voice characters. The actual amount is often
lower; unused allowance is released when the job finishes. Every paid call is
checked before sending. A script longer than the voice allowance stops before
voice generation. Dry runs and free source discovery remain available even if
the spending limit has been reached.

New AI calls are recorded in `cost_events`, including failed runs. Voice calls
use `video.elevenlabs_usd_per_1000_chars` and the selected model in the channel
YAML. This is an estimate, not an invoice: subscriptions and included credits
change the billed amount. Cached results add no new charge. Requests without a
known response retain a conservative **uncertain** estimate. API requests do not
silently retry a possibly charged request.

The ledger combines new cost events, manual entries and earlier saved video
costs, subtracting linked cost events from legacy totals to avoid double-counting.
Older failed runs and voice charges were not reliably recorded; reconcile those
with manual expenses. The old CSV is retained as a run log, not imported as a
second copy of database spending. CLI runs record costs too; the **daily cap is
for browser jobs**, not a global billing lock on independently started CLI runs.

New timestamps use UTC. Historical local timestamps are interpreted using the
server's local time zone for daily spending checks. Manual money entries use the
date you enter.

Jobs are saved in SQLite before a separate Python process starts. Reloading or
double-clicking a submitted form does not start it twice. Capacity and spending
reservations happen inside one database transaction. Workers record a heartbeat;
a stopped worker becomes **interrupted**, with no automatic paid retry. Review
its draft and uncertain charges before starting again. Active renders cannot be
approved or picked up by the scheduler. Videos with publishing history cannot be
re-rendered over their existing output.

## Connect accounts

Connections can be added on each channel page using official OAuth or an existing
access token. Tokens are verified before saving, encrypted with PyCA Fernet, and
never shown in HTML or stored in browser cookies. An account can belong to only
one channel. Importing the old `.env` token is an explicit action for a selected
channel; its mere presence does not mean that channel is connected.

### TikTok

Set these in `.env` and restart the dashboard:

```dotenv
# App credentials from the TikTok developer portal.
TIKTOK_CLIENT_KEY=your-client-key
TIKTOK_CLIENT_SECRET=your-client-secret
# Must exactly match the callback registered with the developer app.
TIKTOK_REDIRECT_URI=https://your-approved-dashboard-domain/oauth/tiktok/callback
```

The login flow requests `user.info.basic`, `user.info.stats`, `video.list` and
`video.upload`. The app needs permission to use those scopes. Token expiry is
read from the returned `expires_in`; refresh tokens are replaced when rotated.
Pasted tokens have no known refresh credentials and may need manual renewal.

### Instagram

This implementation uses **Instagram API with Facebook Login**, for a professional
Instagram account linked to a Facebook Page. It does not interchange tokens from
Meta's separate Instagram Login product.

```dotenv
# Credentials of your Meta developer app.
META_APP_ID=your-app-id
META_APP_SECRET=your-app-secret
# Must exactly match the registered callback.
INSTAGRAM_REDIRECT_URI=https://your-approved-dashboard-domain/oauth/instagram/callback
# Change deliberately when migrating to another supported Graph version.
META_GRAPH_VERSION=v24.0
```

Enter the numeric Instagram professional account ID on the connection form. The
flow requests `instagram_basic`, `instagram_manage_insights`, `pages_show_list`
and `pages_read_engagement`, exchanges the token for a long-lived user token, and
verifies access to that account. Reconnect expired Meta tokens. App approval and
account-role requirements still apply; they cannot be validated without your
real developer app and account.

### Encryption and daily collection

By default the account encryption key is derived from `SESSION_SECRET` with a
separate purpose label. Changing that secret logs browsers out **and requires
reconnecting accounts**. To keep account encryption independent, set a dedicated
`ACCOUNT_TOKEN_KEY` **before** adding connections:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Keep the generated value only in `.env` and back it up privately with the database.
Do not change this key without reconnecting accounts. Disconnecting removes the
local credentials and follower history; revoke app permissions in platform
settings if desired.

While the dashboard runs, a background collector checks for missing daily account
and public-post snapshots every fifteen minutes. Repeated collections skip
successful snapshots from the same UTC day. Separate collection locks prevent
simultaneous manual and automatic requests from duplicating records. Errors appear
on channel pages; missing API results are not saved as zero counts.

Set `DASHBOARD_AUTO_COLLECT=false` to turn off that loop. A server scheduler can
instead invoke the same collector once daily:

```bash
python -m src.analytics.accounts --dry-run
python -m src.analytics.accounts --channel news
```

The TikTok scheduler now uses the channel's encrypted connection. Legacy global
tokens require the explicit channel option `publishing.use_legacy_env_tokens`;
leave it false for multiple channels. Instagram automatic publishing still needs
a separate supported upload/hosting integration. No hosting or public access was
enabled by this update.

## Architecture and verification

```mermaid
flowchart LR
    Browser --> Login[Login and form-token checks]
    Login --> Pages[Jinja2 pages]
    Pages --> DB[(SQLite)]
    Pages --> Job[Saved job + cost reservation]
    Job --> Worker[Separate Python worker]
    Worker --> Sources[Configured sources / checked public article]
    Sources --> AI[Shared LLM client + cache]
    AI --> Draft[Sourced draft]
    Draft --> Render[Voice + Pillow + FFmpeg]
    Render --> Review[Human review]
    Worker --> DB
    Accounts[Encrypted channel connections] --> Collector[Daily official API collector]
    Collector --> DB
```

```bash
pytest -q
pytest -q tests/test_control_center.py
```

Tests use temporary databases and mocked platform/AI requests. They cover login
and form protection, encrypted tokens, one-use OAuth state, refresh rotation,
latest-snapshot totals, duplicate submissions, concurrent job admission, spending
limits, source failures, SSRF/redirect defenses, successful/failed renders, real
subprocess startup, private range requests, and local FFmpeg media previews when
FFmpeg is installed. No paid API requests are needed.

Developer references:

- [TikTok user information](https://developers.tiktok.com/docs/en/tiktok-api-v2-get-user-info)
- [TikTok video queries](https://developers.tiktok.com/docs/en/tiktok-api-v2-video-query)
- [TikTok OAuth token lifecycle](https://developers.tiktok.com/docs/en/oauth-user-access-token-management)
- [Meta's official Instagram API collection](https://www.postman.com/meta/instagram/collection/6yqw8pt/instagram-api)
- [PyCA cryptography](https://github.com/pyca/cryptography) — selected over lower-level alternatives for its adoption and authenticated-encryption recipe.

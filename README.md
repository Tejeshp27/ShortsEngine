# ShortsEngine

An end-to-end content automation pipeline that finds breaking news, uses Claude AI
to write neutral short-form video scripts, renders captioned MP4s, and runs them
through a human review queue. A private dashboard manages channels, accounts,
production, spending and performance. TikTok draft uploads are supported;
Instagram automatic publishing remains unfinished.

Built as a portfolio project demonstrating real-world software engineering: REST APIs,
AI integration, OAuth, database design, data pipelines, Docker, and CI/CD.

---

## Architecture

```
RSS feeds ──► dedupe + filter ──► Claude Haiku (rank) ──► Claude Sonnet (write)
                                                                    │
                                                           output/*.md + DB (drafted)
                                                                    │
                                        FastAPI dashboard ──► human review ──► approved
                                                                                    │
                                        edge-tts (voiceover) + Pillow (frames) + FFmpeg
                                                                                    │
                                                                              9:16 MP4
                                                                                    │
                                        TikTok Content Posting API / Instagram Graph API
```

---

## What it does

1. **Fetches** stories from RSS feeds (NPR, PBS NewsHour, BBC)
2. **Ranks** them with Claude Haiku (~$0.001/run) to find the most newsworthy story
3. **Writes** a neutral, sourced script with Claude Sonnet — hook, story, why it matters, what's next
4. **Saves** every script to the database as `drafted` for human review
5. **Renders** a fully animated 9:16 MP4: stock footage playing full-screen the whole time,
   an animated graphic for every sentence, and word-by-word captions (see below)
6. **Approves** scripts and videos through a FastAPI web dashboard
7. **Uploads** to TikTok drafts; Instagram publishing still needs implementation
8. **Collects daily account and public-post metrics** from connected accounts and
   feeds saved performance back into ranking; live connections require approved platform access

A typical full run costs **$0.04–$0.06** in AI API calls (script ≈ 1–3¢, animated storyboard ≈ 3¢).

---

## Tech stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Language | Python 3.11 | Industry standard for data/AI pipelines |
| AI | Anthropic Claude (Haiku 4.5 + Sonnet 4.6) | Best-in-class reasoning; dual-model cost control |
| News | RSS + feedparser | Free; no scraping ToS issues |
| Web framework | FastAPI + uvicorn | Industry standard, async, auto-docs, used at scale |
| Database | SQLite (Postgres upgrade path) | Zero-setup for development; easy migration |
| Video | edge-tts, Pillow, FFmpeg; optional ElevenLabs | Free voice/rendering path, with tracked estimates for optional paid voice |
| Publishing | TikTok Content Posting API, Instagram Graph API | Official APIs only |
| Auth | bcrypt passwords, signed sessions and encrypted per-channel OAuth tokens | Platform flows still need approved apps and live credential testing |
| Testing | pytest | Industry standard Python test framework |
| CI/CD | GitHub Actions | Runs tests on every push; green checkmarks on the repo |
| Deployment | Docker + Docker Compose | Same setup locally and on the server |

---

## Setup

**Requirements:** Python 3.11+, FFmpeg (`brew install ffmpeg`), Anthropic API key

```bash
# 1. Clone the repo
git clone https://github.com/Tejeshp27/ShortsEngine.git
cd ShortsEngine

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate      # Mac / Linux
# .venv\Scripts\activate       # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Add your API key
cp .env.example .env
# Open .env and add your ANTHROPIC_API_KEY
```

---

## Running the pipeline

```bash
# See today's new stories (free — no AI calls)
python -m src.main --channel news --list

# Test the whole pipeline without spending anything
python -m src.main --channel news --dry-run

# Real run: AI picks a story and writes the script (~$0.01–0.03)
python -m src.main --channel news

# Choose the story yourself (number from --list)
python -m src.main --channel news --pick 3

# Review and approve drafted scripts
python -m src.main --channel news --review

# Render an approved script into a fully animated video (~3 cents)
python -m src.main --channel news --render --animated

# Classic render: one still picture per caption (free, no AI call)
python -m src.main --channel news --render

# Print a cost and activity report
python -m src.main --report
```

---

## Animated videos

`--render --animated` builds every frame of the video in three layers:

| Layer | What it is | Code |
|-------|-----------|------|
| 1. Footage | A real stock video clip playing full-screen, so the background is never blank or still | `src/video/animation/footage.py` |
| 2. Graphic | One animated graphic per sentence — nine templates: headline, name card, icon label, counting number, versus, bullet list, timeline, bar chart, stamp | `src/video/animation/scenes.py` |
| 3. Overlays | Word-by-word captions with the spoken word highlighted, a progress bar, a section label, and a wipe between sections | `src/video/animation/overlays.py` |

**How it works**

1. **Storyboard** (`storyboard.py`) — the script is split into "beats" (a sentence, or part of a
   long one). One AI call picks a footage topic and a graphic for every beat. The result is
   saved as `<script>_storyboard.json` next to the script, so re-rendering is free and any
   scene can be edited by hand.
2. **Accuracy checks** — the AI's answer is never trusted blindly. On-screen words must come
   from the sentence being spoken, numbers and names must be in the script, and a claim must
   keep its "says" / "argues" (so *"Massie argues the law was broken"* can never appear as
   *"THE LAW WAS BROKEN"*). Anything that fails is replaced by a plain headline of the spoken
   words. The render then prints every graphic beside its spoken words for the human fact-check.
3. **Footage** (`footage.py`) — stock video search is unreliable (searching "white house" on
   Pixabay returns cats), so each channel lists hand-checked `footage_topics` in its YAML and
   the AI may only choose from that list.
4. **Render loop** (`renderer.py`) — for each of the ~30 frames per second: take the next
   picture from the footage clip, draw the graphic and overlays on it with Pillow, and pipe it
   straight to FFmpeg. A 95-second video renders in about 90 seconds.

Colours, frame rate, footage topics and the storyboard model are all per-channel settings
under `video.animation` and `models.storyboard` in the channel YAML. Icons are Google's
Noto Emoji (Apache 2.0) and the font is Montserrat (SIL Open Font License) — both free for
commercial use.

---

## Dashboard

```bash
python -m src.dashboard.set_password   # first time only: create your login
python -m src.dashboard.app
# Open http://localhost:8000 and log in
```

Features: an overview with channel totals and history charts, per-channel account
connections, daily statistics, a private searchable video library with playback,
a review queue, payouts and spending, and source-based video creation in background
jobs. The interface uses warm neutral colors and a restrained olive accent.

**Restart the dashboard after upgrading** and install the updated requirements.
Your existing login and videos remain intact. New tables are added automatically.

- Open **New video** to find stories or paste an article URL. Choose a script or
  an animated video; every result stays drafted until you review it.
- Browser jobs default to a free voice, show a conservative cost allowance, and
  support a free dry run. Default limits: **$2 per UTC day**, **one job in progress**.
- Open **Channels** to connect verified TikTok/Instagram accounts. New tokens are
  encrypted per channel. Live OAuth still needs your approved developer apps.
- Open **Money** to record payouts and expenses. New AI/voice estimates are tracked
  in the database; earlier untracked costs may require manual reconciliation.
- Open **Video library** to search, watch, and review files. MP4s without database
  records are flagged. Videos and thumbnails require login.

[Full control-center setup, spending rules, account requirements and architecture](docs/control-center.md).
No hosting was enabled, and no videos are published by browser jobs.

**The dashboard is private by default.** A single check runs before every request, and
anything other than the login page and public static assets is refused unless the browser is
logged in — so a page added later is protected automatically.

| Protection | How |
|-----------|-----|
| Password stored safely | Hashed with bcrypt in the `users` table; never in a file or in git |
| Login sessions | Signed cookie (`SESSION_SECRET` in `.env`), `HttpOnly`, `SameSite=Lax`, optional `Secure` |
| Guessing limit | 5 wrong passwords lock that address out for 15 minutes (`config/dashboard.yaml`) |
| Forged requests | Every form carries a secret token; forms without it are refused |
| Injected HTML | Jinja2 templates escape all text from the database |
| Old sessions | Changing the password signs out every device |
| Network | Listens on `127.0.0.1` only unless `DASHBOARD_HOST` says otherwise |

The login rules are in `src/dashboard/auth.py`, with tests in `tests/test_dashboard_auth.py`.

---

## Project structure

```
shortsengine/
  channels/             # one YAML config per channel (news.yaml, twitch_example.yaml)
  config/               # shared settings (model price list)
  src/
    sources/            # RSS and Twitch news fetchers
    generation/         # Claude script writing (llm.py, prompts.py, ranking.py, writer.py)
    video/              # voiceover (tts.py), captions, Pillow frames, FFmpeg render
      animation/        # animated video engine: storyboard, footage, scenes, renderer
    publishing/         # TikTok and Instagram API clients, scheduler
    storage/            # SQLite schema, all queries in one place, cost reports
    analytics/          # metrics collector, feedback loop
    dashboard/          # FastAPI web app: login (auth.py), pages (app.py), templates/, static/
    main.py             # command-line entry point
  assets/fonts/         # Montserrat font (open licence) used for on-screen text
  docs/                 # deployment guide, resume bullets
  tests/                # 25+ pytest tests
  Dockerfile            # containerised build
  docker-compose.yml    # dashboard + pipeline services
  .github/workflows/    # GitHub Actions CI
```

---

## Multi-channel design

Every channel-specific detail (niche, tone, models, voice, sources, schedule) lives in a
YAML file. Adding a new channel type (e.g. Twitch clips) requires only a new config file —
no Python changes.

```yaml
# channels/news.yaml (excerpt)
name: "News Explained"
niche: "US politics and current events"
audience: "College students who don't follow the news closely"
models:
  ranking: "claude-haiku-4-5"   # cheap — just picks headlines
  writing: "claude-sonnet-4-6"  # stronger — final script
sources:
  - type: rss
    url: "https://feeds.npr.org/1001/rss.xml"
```

---

## Cost controls

| Guard | Location |
|-------|----------|
| Cheap model for ranking, stronger model only for words that get published (script, on-screen text) | `models:` in channel YAML |
| Storyboard saved next to the script — re-rendering a video is free | `<script>_storyboard.json` |
| Stock footage and icons downloaded once and reused | `cache/stock_video/`, `cache/icons/` |
| `max_tokens` and `effort` level on every API call | `llm:` in channel YAML |
| Max AI calls per run guard | `llm.max_calls_per_run` |
| Cached AI answers reused (never pay twice for the same input) | `cache/llm/` |
| Articles downloaded once, HTML stripped, trimmed | `cache/articles/` |
| Already-processed stories skipped | `src/storage/history.py` |
| Prompt caching for the system prompt | `src/generation/llm.py` |
| `--dry-run` mode: full pipeline, zero API spend | `src/main.py` |
| Per-run cost log | `output/cost_log.csv` |

---

## Roadmap

| Phase | Status | Description |
|-------|--------|-------------|
| 0 | ✅ Done | Project setup |
| 1 | ✅ Done | News script generator (Claude AI) |
| 2 | ✅ Done | Video production (TTS + Pillow + FFmpeg) |
| 3 | ✅ Done | SQLite database + human review queue |
| 4 | Partial | TikTok draft uploads; Instagram publishing not implemented |
| 5 | ✅ Done | FastAPI dashboard |
| 6 | ✅ Done | Analytics + feedback loop + Twitch channel |
| 7 | ✅ Done | Docker deploy + GitHub Actions CI + portfolio polish |

---

## Running tests

```bash
pytest -v
```

Tests cover: database layer, caption builder, word-timing estimator, cost parser.
GitHub Actions runs the suite automatically on every push.

---

## Deploying to a server

See [docs/deploy.md](docs/deploy.md) for a step-by-step guide to deploying on a
$6/month DigitalOcean server with Docker, cron scheduling, and optional HTTPS.

```bash
docker compose up -d dashboard
docker compose run --rm pipeline --channel news
```

## Scheduler and spending safeguards

The scheduler reserves one video per channel and scheduled time slot in SQLite.
Repeated runs reuse that reservation and skip destinations that already succeeded.
Set `schedule.timezone` to a named zone such as `America/New_York`; daylight saving
changes and posting windows across midnight are handled automatically. Existing
`utc_offset_hours` settings still work when no named zone is supplied.

A video is marked `posted` only when every configured destination succeeds. For
TikTok this means uploaded to the inbox, not publicly posted. Instagram publishing
is unfinished, so configuring it leaves the video approved. Returned upload IDs are
saved, but automatic metrics collection and OAuth are still unfinished.

Uploads with uncertain outcomes are not automatically repeated: check the platform
first. `publishing_deliveries` records `sending`, `uncertain`, or `succeeded`. After
confirming that an upload did not arrive, delete only its video/platform delivery row
using SQLite to allow a later scheduler run to retry. Interrupted `sending` rows need
the same check. Successful destinations must be kept to prevent duplicate uploads.
A dry run prints the slot without reserving it or uploading anything:

```bash
python -c 'from src.publishing.scheduler import run_scheduler; run_scheduler(dry_run=True)'
```

Analytics totals use the latest cumulative snapshot per video/platform, rather than
adding daily snapshots. Render AI spending is added to the video's database total
even if rendering fails. ElevenLabs voice spending is not yet tracked.

Run the offline regression tests with the virtual environment activated:

```bash
python -m pytest tests/test_review_fixes.py -q
python -m pytest -q
```

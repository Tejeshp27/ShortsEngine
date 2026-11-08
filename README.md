# ShortsEngine

An end-to-end content automation pipeline that finds breaking news, uses Claude AI
to write neutral short-form video scripts, renders captioned MP4s, runs them through
a human review queue, and publishes to TikTok and Instagram.

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
5. **Renders** a 9:16 MP4 with neural TTS voiceover, word-by-word captions and headline overlay
6. **Approves** scripts and videos through a FastAPI web dashboard
7. **Publishes** to TikTok (file upload → drafts) and Instagram Reels
8. **Tracks** views, likes, comments and shares; feeds performance data back into the ranker

A typical full run costs **$0.01–$0.03** in AI API calls.

---

## Tech stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Language | Python 3.11 | Industry standard for data/AI pipelines |
| AI | Anthropic Claude (Haiku 4.5 + Sonnet 4.6) | Best-in-class reasoning; dual-model cost control |
| News | RSS + feedparser | Free; no scraping ToS issues |
| Web framework | FastAPI + uvicorn | Industry standard, async, auto-docs, used at scale |
| Database | SQLite (Postgres upgrade path) | Zero-setup for development; easy migration |
| Video | edge-tts, Pillow, FFmpeg | Fully free local stack; no paid voice API |
| Publishing | TikTok Content Posting API, Instagram Graph API | Official APIs only |
| Auth | OAuth 2.0 (TikTok, Instagram), HTTP Basic (dashboard) | Standard industry patterns |
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

# Render an approved script into a video
python -m src.main --channel news --render

# Print a cost and activity report
python -m src.main --report
```

---

## Dashboard

```bash
python -m src.dashboard.app
# Open http://localhost:8000
```

Features: stats cards, review queue (approve/reject in browser), per-channel analytics,
channel list. Protected by `DASHBOARD_PASSWORD` in `.env`.

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
    publishing/         # TikTok and Instagram API clients, scheduler
    storage/            # SQLite schema, all queries in one place, cost reports
    analytics/          # metrics collector, feedback loop
    dashboard/          # FastAPI web app
    main.py             # command-line entry point
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
| Cheap model for ranking, stronger model only for writing | `models:` in channel YAML |
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
| 4 | ✅ Done | TikTok + Instagram publishing |
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

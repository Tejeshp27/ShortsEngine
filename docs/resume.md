# ShortsEngine — Resume Bullets

Use these on your resume under Projects. Pick the bullets that fit the word count.

---

## Short version (1–2 lines)

> **ShortsEngine** | Python, FastAPI, SQLite, Anthropic Claude, TikTok API, Instagram Graph API
> Automated short-form video pipeline that fetches news via RSS, uses Claude AI to rank stories and write neutral scripts, renders captioned MP4s with TTS + FFmpeg, and publishes to TikTok and Instagram through a FastAPI review dashboard.

---

## Full bullet list (pick 3–5)

**Core pipeline**
- Built an end-to-end content automation system in Python that fetches live news from RSS feeds, deduplicates stories, ranks them with Claude Haiku (cheap model), writes neutral video scripts with Claude Sonnet, and assembles captioned MP4s with edge-tts voiceover and Pillow/FFmpeg.

**AI integration**
- Designed a reusable LLM client (`llm.py`) with prompt caching, batch-friendly cost tracking (tokens × price per million), `--dry-run` mode, and a per-run spending cap — keeping costs under $0.02 per video.

**REST API and web dashboard**
- Developed a FastAPI dashboard with HTTP Basic Auth, a video review queue (approve/reject before publishing), per-channel analytics, and a cost report — deployable locally or in Docker.

**Publishing APIs**
- Integrated the TikTok Content Posting API (file upload → drafts flow) and the Instagram Graph API; OAuth callback routes handle token exchange; scheduler posts approved videos at configured times.

**Database design**
- Designed a SQLite schema (`videos`, `cost_events`, `metrics` tables) with foreign keys and status state machine (drafted → approved → posted); all queries in a single `queries.py` module for easy migration to Postgres.

**Analytics feedback loop**
- Built a daily metrics collector that pulls view/like/comment counts from TikTok and Instagram APIs, stores them in SQLite, and injects performance history into the story ranker's prompt — closing the AI feedback loop.

**Extensible channel system**
- Designed the system around per-channel YAML configs (tone, voice, models, sources, schedule); adding a new channel (e.g. Twitch clips) requires only a new YAML file and no Python changes.

**Testing and CI**
- Wrote 25+ pytest tests covering the database layer, caption builder, TTS timing estimator, and cost parser; GitHub Actions CI runs on every push with a dummy API key (no real calls in CI).

**DevOps**
- Dockerized the full stack (`Dockerfile` + `docker-compose.yml`) and wrote a step-by-step deployment guide for a $6/month DigitalOcean server with cron scheduling and optional HTTPS via Caddy.

---

## Skills this project demonstrates

| Area | Technologies |
|------|-------------|
| Language | Python 3.11 |
| AI / LLM | Anthropic Claude API (Haiku + Sonnet), prompt caching, cost tracking |
| Web framework | FastAPI, uvicorn |
| Database | SQLite (with Postgres upgrade path), raw SQL, schema design |
| APIs | TikTok Content Posting API, Instagram Graph API (Meta), Twitch Helix API |
| Video | FFmpeg, edge-tts (neural TTS), Pillow (image generation), ASS captions |
| DevOps | Docker, Docker Compose, GitHub Actions CI/CD |
| Testing | pytest |
| Other | RSS/feedparser, YAML config, OAuth 2.0, HTTP Basic Auth |

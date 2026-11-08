# CLAUDE.md — ShortsEngine Project Instructions

## What this project is

**ShortsEngine** is a multi-channel content automation platform. It finds news and other
source material, uses AI to write and produce original short-form videos, sends them
through a human review step, publishes them to TikTok and Instagram, and tracks how
each video performs.

It has two goals:
1. **Portfolio project:** Show real software engineering (APIs, AI integration, OAuth,
   databases, data pipelines, an analytics dashboard). Code quality, structure and
   documentation matter as much as features.
2. **Real channels:** Grow accounts that could eventually earn money. The first channel
   is a news channel that explains politics and current events in an interesting,
   easy-to-understand way.

## About the developer

I am a CS & Data Science student directing this project with Claude Code. I must be
able to read, understand and **explain every part of it in an interview**.

## How to write code

**Priority order:** 1) beginner-friendly, 2) efficient and correct. Write the simplest
version first. Only use a more advanced approach when the simple one would be clearly
slow, wasteful or wrong, and when you do, explain why in a comment.

**Beginner-level syntax and format, always:**
- Clear, descriptive names (`fetch_top_stories`, not `fts` or `get_data`).
- Short functions that each do one thing.
- Plain `for` loops and `if` statements instead of dense comprehensions, lambdas,
  nested ternaries or chained one-liners.
- Avoid advanced features (decorators, metaclasses, generators, complex inheritance,
  heavy type tricks) unless truly needed. If one is needed, explain it in a comment.
- Type hints are fine when they make the code clearer.

**Descriptions for everything:**
- Every file starts with a comment block: what the file is for and how it fits into
  the project.
- Every function has a docstring: what it does, its inputs, what it returns, and an
  example where helpful.
- Add comments above any step that isn't obvious, explaining *why*, not just *what*.
- Config files (YAML, `.env.example`) have a comment for every setting.

**After each change**, give me a short plain-English summary: what you built, which
files changed, how it works, and how to run or test it.

If a request is unclear or there is a better approach, ask or suggest it before
building.

## Keeping costs as low as possible

API costs must stay minimal. Always choose the cheapest option that does the job well.

- **Use the cheapest model that works.** Use a small, cheap model (e.g. Claude Haiku)
  for simple tasks like ranking headlines, tagging topics and short summaries. Use a
  stronger model (e.g. Claude Sonnet) only for the final script writing. Model names
  live in the channel/config files, not in the code.
- **Send less text.** Trim articles to the relevant parts, strip HTML, and set a
  `max_tokens` limit on every call.
- **Never pay twice for the same thing.** Cache fetched articles and LLM results
  on disk or in the database. Skip stories that were already processed.
- **Use prompt caching** for long system prompts that repeat on every call.
- **Use batch processing** (e.g. Anthropic's Message Batches API, about 50% cheaper)
  for any work that doesn't need an instant result.
- **Prefer free or local tools** when the quality is good enough: free RSS feeds,
  local Whisper for transcription, FFmpeg for video, and a free or local text-to-speech
  option before a paid voice service.
- **Spending guards:** a `--dry-run` mode that makes no paid API calls, a maximum
  number of LLM calls per run, and a log of the estimated cost of every run
  (tokens in/out × price) so I can see what I'm spending.
- Before adding any new paid API or service, tell me what it costs and suggest a
  free or cheaper alternative if one exists.

## Core design rule: everything is per channel

The system must support **many channels**, not just the news channel. Never hard-code
channel-specific details (topics, tone, sources, models, voice, schedule) in Python
code. They belong in a channel config file, for example:

```yaml
# channels/news.yaml
name: "News Explained"                  # display name of the channel
niche: "US politics and current events" # what the channel covers
audience: "College students who don't follow the news closely"
tone: "Neutral, clear, engaging; explain jargon; present each side fairly"
target_length_seconds: 75               # target video length
models:
  ranking: "claude-haiku-5-5"           # cheap model for picking stories
  writing: "claude-sonnet-5-5"          # stronger model for the final script
sources:
  - type: rss
    url: "https://feeds.npr.org/1001/rss.xml"
platforms: [tiktok, instagram]
```

Adding a new channel should only mean adding a new YAML file.

## Tech stack

- **Language:** Python 3.11+
- **LLM:** Anthropic API (Claude). All LLM calls go through one small module
  (`llm.py`) so the provider can be swapped and costs can be tracked in one place.
- **News sources (Phase 1):** Public RSS feeds via `feedparser` (NPR, PBS NewsHour,
  BBC US & Canada)
- **Config:** YAML files, one per channel. Secrets in `.env`.
- **Database:** SQLite to start, Postgres later if needed
- **Backend / dashboard:** FastAPI and a simple web frontend
- **Video:** FFmpeg, a text-to-speech voice, and captions generated from the script
- **Dependencies:** listed in `requirements.txt`; use a virtual environment (`.venv`)

## Planned folder structure

```
shortsengine/
  CLAUDE.md
  README.md
  requirements.txt
  .env.example          # lists required keys, no real values
  channels/             # one YAML config per channel
  src/
    sources/            # fetching news / clips (rss.py, twitch.py later)
    generation/         # script writing (llm.py, prompts.py)
    video/              # voiceover, captions, rendering
    publishing/         # TikTok / Instagram APIs
    storage/            # database models and queries
    main.py             # command-line entry point
  output/               # generated scripts / videos (git-ignored)
  cache/                # cached articles and LLM results (git-ignored)
  tests/
```

## Content rules (news channel)

- **Accuracy first.** Scripts may only use facts found in the source articles provided.
  Never invent quotes, numbers, names or events. If something is unclear, say so in the
  script notes instead of guessing.
- **Every script lists its sources** (title and URL) at the bottom, for my fact-check.
- **Neutral and fair.** Explain what happened, why it matters and what each major side
  says. No opinions, no loaded language, no telling viewers what to think.
- **Script format:** a hook (first 3 seconds), the story, why it matters, what happens
  next, and a closing line. Aim for the channel's `target_length_seconds`.
- **Human review is mandatory.** Nothing is ever published without my approval.

## Safety and security rules

- **Never commit secrets.** API keys live only in `.env`, which is in `.gitignore`.
  Keep `.env.example` updated with key *names* only.
- Only use official APIs and content we have rights to use. Do not scrape sites in ways
  that break their terms, and do not download copyrighted video (for example, network
  news broadcasts).
- Respect platform rules: no automated public posting until each platform's API access
  is properly approved.

## How to work

1. Build **one small feature at a time**. Tell me how to run it, then wait for me to
   confirm it works.
2. Prefer simple, working code over a big framework.
3. Handle errors with clear messages (for example, "Missing ANTHROPIC_API_KEY in .env").
4. Add a basic test for any core logic you write.
5. Suggest a git commit with a clear message after each working step.
6. Keep `README.md` up to date: what the project does, setup steps and how to run it.
   It is part of my portfolio.

## Current phase

**Phase 1: news script generator.** Do not start a later phase until I say so.

## Roadmap

Each phase ends with something that works on its own. Finish one before starting the next.

### Phase 0 — Project setup
- Folder structure, virtual environment, `requirements.txt`, `.gitignore`,
  `.env.example`, starter `README.md`, first git commit.
- **Done when:** the project runs a "hello world" command and no secrets are tracked by git.

### Phase 1 — News script generator (text only)
- Load the channel config, fetch stories from RSS, remove duplicates, rank them with
  the cheap model (or let me pick from a list), and write a neutral script with the
  writing model.
- Save each script to `output/<channel>/<date>-<slug>.md` with its sources.
- Includes caching, `--dry-run` and cost logging.
- **Done when:** one command produces a good, sourced script for under a few cents.

### Phase 2 — Video production
- Text-to-speech voiceover, burned-in word-by-word captions, simple visuals (headline
  text, maps, charts, backgrounds), and a 9:16 MP4 rendered with FFmpeg.
- **Done when:** one command turns an approved script into a ready-to-post video.
  (I post these by hand during this phase to test what gets views.)

### Phase 3 — Database and review queue
- SQLite tables for channels, videos (status: drafted → approved → posted → failed),
  and costs.
- A simple command-line or web review step where I approve, edit or reject scripts
  and videos.
- **Done when:** every video and its cost is tracked, and nothing moves forward
  without my approval.

### Phase 4 — Publishing
- Instagram Graph API publishing for Reels, and the TikTok Content Posting API
  (upload-to-drafts until the app passes TikTok's audit).
- Scheduled posting at set times per channel.
- **Done when:** approved videos post (or land in TikTok drafts) automatically.

### Phase 5 — Dashboard and connected accounts
- FastAPI web app: create and edit channels, connect TikTok/Instagram accounts with
  OAuth (tokens stored encrypted), use the review queue, and see stats per channel
  (videos made, posted, views, API spend, top performers).
- **Done when:** I can manage every channel from the browser.

### Phase 6 — Analytics loop and more channels
- Collect views, likes, shares and watch time daily; show trends; use the results to
  improve story selection and hooks.
- Add a second channel type (e.g. Twitch clips with streamer permission).
- **Done when:** there's a measurable improvement I can put on my resume.

### Phase 7 — Polish for the portfolio
- Deploy to a cheap cloud server, scheduled runs, tests, a clean README with an
  architecture diagram and screenshots, and a short demo video.

## Commands

*(Fill these in as the project grows.)*

- Set up: `python -m venv .venv` → activate → `pip install -r requirements.txt`
- Run Phase 1: `python -m src.main --channel news`
- Dry run (no paid calls): `python -m src.main --channel news --dry-run`
- Test: `pytest`

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
- Every function, method, and class must have a docstring written in plain English,
  as if explaining it to someone seeing Python for the first time. Cover: what it does
  in one sentence, what each input means, what it returns, and a short example where
  that helps. Avoid technical jargon in the docstring itself — use a follow-up comment
  if you need to explain an implementation detail.
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
  stronger model (e.g. Claude Sonnet) only for words that get published: the final
  script, and the on-screen text of animated videos (the storyboard). Model names
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
  ranking: "claude-haiku-4-5"           # cheap model for picking stories
  writing: "claude-sonnet-4-6"          # stronger model for the final script
sources:
  - type: rss
    url: "https://feeds.npr.org/1001/rss.xml"
platforms: [tiktok, instagram]
```

Adding a new channel should only mean adding a new YAML file.

## Tech stack

Always choose the tool that is **most widely used in the industry** for that job. The
goal is a portfolio that shows the same stack a hiring manager at an AI company or top
tech company would expect to see. When a simpler but less standard tool would work,
use the standard one and note why.

- **Language:** Python 3.11+ — the dominant language for AI/ML and backend work.
- **LLM:** Anthropic API (Claude) via the official `anthropic` Python SDK. All LLM
  calls go through one small module (`llm.py`) so the provider can be swapped and
  costs can be tracked in one place.
- **Data validation:** `pydantic` — the standard for data models and config parsing
  in Python; used heavily in FastAPI and AI pipelines at real companies.
- **Web framework:** `FastAPI` — the fastest-growing Python API framework; async,
  OpenAPI docs generated automatically, used by companies like Netflix and Uber.
- **Database:** SQLite for local dev, PostgreSQL for production. Access via raw SQL
  (not an ORM) so the queries are readable and easy to explain.
- **Frontend (Phase 5+):** React + TypeScript — the most in-demand frontend stack.
  TypeScript is expected at almost every company hiring frontend engineers.
- **Video:** FFmpeg (industry standard for video processing) + `edge-tts` (free
  neural TTS; swap for ElevenLabs later if quality needs to improve).
- **Testing:** `pytest` — the standard Python test framework.
- **CI/CD (Phase 7):** GitHub Actions — the most common CI system; free for public
  repos and expected knowledge in most engineering interviews.
- **Containerisation (Phase 7):** Docker — standard for reproducible deployments;
  knowing Docker is expected at almost every SWE role.
- **News sources:** Public RSS feeds via `feedparser`.
- **Config:** YAML files, one per channel. Secrets in `.env` via `python-dotenv`.
- **Dependencies:** `requirements.txt`; use a virtual environment (`.venv`).

**Rule:** Before picking any library, check that it is the most-used option for that
job (by GitHub stars, PyPI downloads, or industry surveys such as the Stack Overflow
Developer Survey). If a less popular library is chosen for a good reason (e.g. cost,
licence), add a comment explaining the trade-off.

## Planned folder structure

```
shortsengine/
  CLAUDE.md
  README.md
  requirements.txt
  .env.example          # lists required keys, no real values
  channels/             # one YAML config per channel
  config/               # shared settings, e.g. pricing.yaml (model prices)
  src/
    sources/            # fetching news / clips (rss.py, twitch.py later)
    generation/         # script writing (llm.py, prompts.py)
    video/              # voiceover, captions, rendering
      animation/        # animated video engine (storyboard, footage, scenes, renderer)
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
7. **Updating this file:** You may update `CLAUDE.md` at any time without asking
   permission. Always tell me after you do, and briefly explain what changed and why.

## Current phase

**Phases 1–7 code complete. M-series visual upgrade (M.1–M.6) code complete.
A-series animated video engine (A.1–A.5) code complete — this is now the main way to render.**

- Phase 1: scripts generate for ~$0.013 ✅
- Phase 2: `--render` produces a real 9:16 MP4 (Pillow + FFmpeg + macOS TTS) ✅
- Phase 3: `--review` queue and `--report` work ✅
- Phase 4: publishing code built; needs INSTAGRAM_ACCESS_TOKEN / TIKTOK_ACCESS_TOKEN in .env ✅
- Phase 5: FastAPI dashboard runs at http://localhost:8000 (`python -m src.dashboard.app`) ✅
- Phase 6: analytics collector + feedback loop built; needs posted videos for data ✅
- Phase 7: Dockerfile, GitHub Actions CI (.github/workflows/tests.yml), README, docs/ ✅
- M.1: analysis of legal footage sources and animation tools ✅
- M.2: Claude Haiku scene planner (`scene_planner.py`) — 5 scene types ✅
- M.3: Pexels Video B-roll integration (`stock_video.py`) — needs PEXELS_API_KEY ✅
- M.4: Pillow-based animated graphics (`animated_frames.py`) — no extra deps ✅
- M.5: Renderer updated to wire all M-series types + `--style` flag ✅
- M.6: Tests for scene planner, animated frames, stock video ✅
- A.1–A.5: Animated video engine (`src/video/animation/`, `--render --animated`) ✅

**Next human steps:**
- Watch the animated render of the impeachment script and give feedback on the look
- Grow `footage_topics` in `channels/news.yaml` (more approved topics = more variety)
- Add PEXELS_API_KEY to .env (free at pexels.com/api) — Pexels has far better footage
  than Pixabay, but its results must be re-checked before raising the topic counts
- For Manim animated scenes (richer diagrams): `brew install cairo pango && pip install manim`
- Post videos by hand, then add API tokens for automated publishing (Phase 4)

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

### M-series — Visual quality upgrade (runs alongside Phase 7+)

The M-series improves video quality without changing the core pipeline.

**M.1 — Legal footage analysis** ✅
- Identified three free sources: Pexels Video (commercial licence), Pixabay Video,
  and US government YouTube.
- Chose Pillow animations over Manim for data graphics (no system-library deps);
  Manim available as an upgrade when `brew install cairo pango`.

**M.2 — Scene plan JSON** ✅ (`src/video/scene_planner.py`)
- Claude Haiku reads the spoken script and assigns a scene type to each ~15-word
  segment: `footage`, `image`, `animated`, `chart`, or `text_card`.
- Saved as `<slug>_sceneplan.json` next to the script for review/editing.

**M.3 — Pexels Video B-roll** ✅ (`src/video/stock_video.py`)
- Searches Pexels Video API for a clip matching the segment description.
- Downloads and caches the clip; extracts one frame per caption group so the
  background "plays" through the clip while captions advance.
- Needs PEXELS_API_KEY in .env (free at pexels.com/api).

**M.4 — Pillow animated graphics** ✅ (`src/video/animated_frames.py`)
- Two animation types, both drawn with Pillow only (no extra dependencies):
  - Number callout: a large stat counts up frame by frame (e.g. "27,000 ft").
  - Comparison bars: two horizontal bars grow side by side.

**M.5 — Renderer update** ✅ (updated `src/video/render.py`, `src/main.py`)
- `render.py` handles all 5 scene types in `_build_visual_frames()`.
- Footage and animated frames skip Ken Burns (they already change per caption group).
- New `--style` flag: `auto` (Haiku decides), `footage` (prefer video), `animated`
  (prefer graphics for data segments).

**M.6 — Tests** ✅ (`tests/test_scene_planner.py`)
- 30 tests covering scene_planner, animated_frames, and stock_video.
- All run offline (dry_run + mocks) — no API calls needed.

### A-series — Animated video engine (`src/video/animation/`)

Run with `python -m src.main --channel news --render --animated`. Every frame is three
layers: stock footage playing full-screen, an animated graphic for the current sentence,
and word-by-word captions. There is never a blank or still screen.

**A.1 — Storyboard** ✅ (`storyboard.py`)
- Splits the script into beats (a sentence or part of one, max 12 words).
- One AI call (`models.storyboard`, ~3 cents) picks a footage topic and a graphic per beat.
- Saved as `<slug>_storyboard.json` next to the script: free to re-render, editable by hand.

**A.2 — Accuracy checks** ✅ (`storyboard.py`)
- On-screen words must come from the spoken sentence; numbers and names must be in the
  script; claims must keep "says" / "argues" / "expected". Failures become a plain
  headline of the spoken words. Every graphic is printed beside its spoken words for review.
- Haiku was tried first and rejected: it stated claims as facts and ignored the rules.

**A.3 — Footage** ✅ (`footage.py`, `stock_video.get_video_clip`)
- Clips come only from the channel's hand-checked `video.animation.footage_topics`
  (free-form Pixabay search returned unusable clips). Dark clips are skipped.
- Fallback order: chosen topic → general topics → previous clip → moving gradient.

**A.4 — Graphics and overlays** ✅ (`scenes.py`, `overlays.py`)
- Nine templates: headline, icon_label, person, big_number, versus, bullet_list,
  timeline, bar_chart, stamp. Icons are Noto Emoji; font is Montserrat (both open licence).
- Captions highlight the spoken word; progress bar; section label; wipe between sections.

**A.5 — Renderer and tests** ✅ (`renderer.py`, `tests/test_animation.py`)
- Draws ~30 frames per second with Pillow and pipes them to FFmpeg (no PNG files).
- 54 offline tests.

**Future ideas for the A-series**
- Sound effects on scene changes (whoosh / pop) and background music by default.
- More graphic templates (map, quote card) and more approved footage topics.

**Future: Manim diagrams (M.4b)**
- Richer data-vis scenes (maps, timelines, motion diagrams) using Manim Community.
- Requires: `brew install cairo pango && pip install manim`.
- Stub is in place; activate by setting `scene_type = "manim"` in scene plan JSON.

## Commands

**First-time setup:**
```bash
python3 -m venv .venv          # create the virtual environment
source .venv/bin/activate      # activate it (Mac/Linux)
pip install -r requirements.txt
cp .env.example .env           # then open .env and add your API key
```

**Every session** — activate the virtual environment first:
```bash
source .venv/bin/activate
```

**Running the pipeline:**
```bash
python -m src.main --channel news --list         # list today's new stories (free)
python -m src.main --channel news --dry-run      # full pipeline, no paid API calls
python -m src.main --channel news                # AI picks a story and writes a script
python -m src.main --channel news --pick 3       # write about story #3 from --list
```

**Making a video:**
```bash
python -m src.main --channel news --review             # approve a drafted script
python -m src.main --channel news --render --animated  # fully animated video (~3 cents)
python -m src.main --channel news --render             # classic still-picture video (free)
```
To change a graphic, edit `output/<channel>/<script>_storyboard.json` and render again (free).

**Tests:**
```bash
pytest          # run all tests
pytest -v       # verbose (shows each test name)
```

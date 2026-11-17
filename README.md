# ShortsEngine

An end-to-end content automation pipeline that finds breaking news, uses Claude AI
to write neutral short-form video scripts, renders captioned MP4s, and runs them
through a human review queue. A private dashboard manages channels, accounts,
production, spending and performance. Approved videos post as Instagram and
Facebook Page Reels and YouTube Shorts, and upload to TikTok drafts, from a
"Post now" button or a schedule.

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
3. **Writes** a neutral, sourced script with Claude Sonnet in the channel's style ("News Made
   Simple": hook → what happened → simple explanation/analogy → why it matters → each side →
   what's next → slogan), then **self-checks** it with Claude Haiku (hook, clarity, neutrality,
   accuracy against the source, plus word-count checks in code) and rewrites once if needed
4. **Saves** every script to the database as `drafted` for human review
5. **Renders** a fully animated 9:16 MP4: a new shot every 1–3 seconds (licensed stock footage
   or real Creative Commons photos of named people and places), an animated graphic for every
   sentence, keyword captions in the safe zone, naturally paced voice, ducking music and sound
   effects at -14 LUFS — after an automatic **quality gate** (see below)
6. **Approves** scripts and videos through a FastAPI web dashboard
7. **Publishes** approved videos as Instagram and Facebook Page Reels (Meta Graph API)
   and YouTube Shorts (YouTube Data API), using resumable uploads so no public
   hosting is needed, and uploads them to TikTok drafts
8. **Collects daily account and public-post metrics** from connected accounts and
   feeds saved performance back into ranking; live connections require approved platform access

A typical full run costs **$0.05–$0.08** in AI API calls (script + self-check ≈ 2–4¢, animated storyboard ≈ 3–4¢), plus the voice (free, or ~$0.30 with ElevenLabs at the configured rate).

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
| Publishing | TikTok Content Posting API, Meta Graph API, YouTube Data API v3 | Official APIs only |
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
| 1. Background | A new shot at least every 2.8 s: a freely licensed photo of the person/place named (Wikimedia Commons, slow zoom) or a hand-checked stock clip (slow pan) | `photos.py`, `footage.py`, `shots.py` |
| 2. Graphic | One animated graphic per sentence — ten templates: headline, name card (with photo), icon label, counting number, versus, analogy, bullet list, timeline, bar chart, stamp | `src/video/animation/scenes.py` |
| 3. Overlays | 1–3-word captions with the keyword in the accent colour, inside the platforms' safe zone; brand bug, section label, progress bar, section wipes and an end card | `src/video/animation/overlays.py` |
| Sound | Paced voice, quiet music that ducks under it, whooshes and pops — CC0 or your own licensed tracks only, mixed to -14 LUFS | `src/video/pacing.py`, `src/video/sound_design.py` |

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

5. **Quality gate** (`quality_gate.py`) — before drawing any frame, the plan is checked: no shot
   over 3 s, every scene has a visual, every clip/photo/sound has a licence and credit, captions
   stay in the safe zone, the soundtrack is about -14 LUFS, and the video is near the target
   length. Errors stop the render; warnings are saved in `<video>_quality.json`. Credits are
   saved in `<video>_credits.txt`. See `docs/video-quality-upgrade.md` for the full audit.

Colours, frame rate, footage topics and the storyboard model are all per-channel settings
under `video.animation` and `models.storyboard` in the channel YAML. Icons are Google's
Noto Emoji (Apache 2.0) and the font is Montserrat (SIL Open Font License) — both free for
commercial use.

---

## Clips channel (Twitch + Kick highlights)

A second channel type, `type: clips` (`channels/twitch_clips.yaml`), turns real moments from
streamers' Twitch and Kick streams into vertical videos for TikTok and YouTube Shorts. The video is **only the real clip**: no
animations, no stock footage, no channel logo. It adds a split screen (webcam on top,
gameplay below), word-by-word captions, a hook title for the first 3 seconds and an on-screen
credit (`🎥 twitch.tv/<streamer>`).

**Permission first.** Only streamers listed in the YAML (with `platform: twitch` or `kick`,
a permission note and a date) can be processed; everyone else gets a clear error. Twitch only
gives download links to the streamer themselves, so each Twitch streamer runs a one-time login
(`connect`) that grants just `channel:manage:clips`; the token is stored encrypted. Kick has no
official clip API, so a Kick streamer sends you clip files and you add them with `upload`.

```bash
python -m src.clips --channel twitch_clips connect <streamer>   # once per streamer
python -m src.clips --channel twitch_clips run --dry-run        # free preview
python -m src.clips --channel twitch_clips run                  # find + download + make videos
python -m src.clips --channel twitch_clips upload clip.mp4 --streamer <kick name> --i-own-this
python -m src.clips --channel twitch_clips list                 # every clip and its step
```

| Step | What happens | Code | Cost |
|------|--------------|------|------|
| Find | Most-viewed clips of the last 24 h per approved streamer (official Get Clips API). Clips already in the database are skipped, so nothing is downloaded twice. | `src/clips/finder.py`, `src/sources/twitch.py` | free |
| Download | Official Get Clips Download API → `cache/clips/` | `finder.py` | free |
| Transcribe | Local Whisper (whisper.cpp, `brew install whisper-cpp`) gives timed words | `src/clips/transcribe.py` | free |
| Pick | Claude Haiku picks the best 20–60 s (or keeps it whole) and writes a hook title of up to 8 words. The code then forces the result inside the limits and never cuts a word in half. | `src/clips/moments.py` | ~$0.001 |
| Render | FFmpeg crops and stacks the webcam and gameplay; Pillow draws the text; FFmpeg encodes 1080×1920 with levelled sound | `src/video/clip_layout.py`, `src/video/clip_render.py` | free |
| Review | A normal drafted video in the review queue. Its page has a **Re-cut** form (new start/end/title, free) and the caption to paste in TikTok | `src/clips/maker.py`, dashboard | free |
| Post | Approved clips go to TikTok **drafts** and **YouTube Shorts** (Gaming category) through the existing scheduler. TikTok's drafts API has no caption field, so you paste the ready-made caption (credit + link + hashtags) in the app; on YouTube it becomes the description, with #Shorts | `src/publishing/` | free |

**Why Kick is manual.** Kick's public API covers channels, chat, livestreams and events, but
has no clip or video endpoint, and this project never scrapes. Uploaded Kick clips still go
through the approved-list check and get the `kick.com/<name>` credit and the streamer's webcam layout.

**TikTok's originality rule.** TikTok does not recommend "unoriginal or reused material
without anything new". Each clip is re-cut to its best moment, re-framed for phones, and
given captions, a hook title and a visible credit. TikTok publishes no exact threshold, so
this lowers the risk but can't guarantee For You feed reach. TikTok also forbids apps adding
promotional watermarks, which is why clips carry no channel logo.

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

- Open **New video** to find stories or paste an article URL. Typing a topic (e.g.
  "NVIDIA") searches recent news across the web as well as the channel's feeds,
  using free services (GDELT, and NewsAPI when a key is set). Choose a script or
  an animated video; every result stays drafted until you review it.
- Browser jobs default to a free voice, show a conservative cost allowance, and
  support a free dry run. Default limits: **$2 per UTC day**, **one video being written or rendered at a
  time** (finding stories and collecting statistics can run alongside it).
- Open **Channels** to connect verified TikTok/Instagram accounts. New tokens are
  encrypted per channel. Live OAuth still needs your approved developer apps.
- Open **Money** to record payouts and expenses. New AI/voice estimates are tracked
  in the database; earlier untracked costs may require manual reconciliation.
- Open **Video library** to search, watch, and review files. MP4s without database
  records are flagged. Videos and thumbnails require login.

[Full control-center setup, spending rules, account requirements and architecture](docs/control-center.md).
No hosting was enabled. The only browser job that posts is **Post now** on an approved video.

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
  channels/             # one YAML config per channel (news.yaml, twitch_clips.yaml)
  config/               # shared settings (model price list)
  src/
    sources/            # RSS, web news search and the official Twitch API
    generation/         # Claude script writing (llm.py, prompts.py, ranking.py, writer.py)
    video/              # voiceover (tts.py), captions, Pillow frames, FFmpeg render
      animation/        # animated video engine: storyboard, footage, scenes, renderer
    publishing/         # TikTok, Instagram, Facebook and YouTube clients, scheduler
    clips/              # clips channel: permissions, find/download, Whisper, moment pick, CLI
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
YAML file. Adding another channel of an existing type (news, or `type: clips`) requires
only a new config file — no Python changes.

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
| 4 | ✅ Done | Instagram + Facebook Reels, YouTube Shorts, TikTok drafts, "Post now" + schedule |
| 5 | ✅ Done | FastAPI dashboard |
| 6 | ✅ Done | Analytics + feedback loop |
| L | ✅ Code done | Twitch + Kick clips channel → TikTok + YouTube (needs a Twitch app and streamer logins) |
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
TikTok this means uploaded to the inbox, not publicly posted. Instagram and Facebook
Reels are public as soon as they succeed. The Instagram Reel ID is saved as a
confirmed public post, so the daily collector gathers its statistics.

The same code runs for the dashboard's **Post now** button and for the schedule.
While the dashboard runs it checks schedules every 5 minutes
(`DASHBOARD_AUTO_PUBLISH=false` turns this off). A failure before Meta's final
publish step left nothing public, so its reservation is released and the next try
repeats only that platform.

Uploads with uncertain outcomes are not automatically repeated: check the platform
first. `publishing_deliveries` records `sending`, `uncertain`, or `succeeded`. After
confirming that an upload did not arrive, delete only its video/platform delivery row
using SQLite to allow a later scheduler run to retry. Interrupted `sending` rows need
the same check. Successful destinations must be kept to prevent duplicate uploads.
A dry run prints the slot without reserving it or uploading anything:

```bash
python -m src.publishing.scheduler --dry-run
```

Analytics totals use the latest cumulative snapshot per video/platform, rather than
adding daily snapshots. Render AI spending is added to the video's database total
even if rendering fails. ElevenLabs voice spending is not yet tracked.

Run the offline regression tests with the virtual environment activated:

```bash
python -m pytest tests/test_review_fixes.py -q
python -m pytest -q
```

## How this project is built

I design and direct ShortsEngine and review every change. **Claude Code is the main
AI coding assistant**; **Codex is used occasionally** for smaller tasks. Both follow the
same rules in [`CLAUDE.md`](CLAUDE.md): `AGENTS.md`, the file Codex reads, simply points
there. Every change needs tests and plain-English documentation so I can explain any
part of the code.

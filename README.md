# ShortsEngine

A multi-channel content automation platform that finds news, uses AI to write short-form video scripts, and publishes them to TikTok and Instagram.

Built as a portfolio project demonstrating: REST APIs, AI integration (Anthropic Claude), OAuth, SQLite/Postgres, data pipelines, and an analytics dashboard.

---

## What it does

1. **Fetches** stories from RSS feeds (NPR, PBS, BBC)
2. **Ranks** them with a cheap AI model to find what's worth covering
3. **Writes** a neutral, well-sourced short-form script with a stronger AI model
4. **Queues** it for human review before anything is posted
5. **Publishes** approved videos to TikTok and Instagram *(Phase 4+)*

The system supports multiple channels — each channel has its own YAML config file that sets the topic, tone, models, and sources.

---

## Setup

**Requirements:** Python 3.11+, an [Anthropic API key](https://console.anthropic.com)

```bash
# 1. Clone the repo
git clone https://github.com/tejeshpatel05/ShortsEngine.git
cd ShortsEngine

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate      # Mac/Linux
# .venv\Scripts\activate       # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Add your API key
cp .env.example .env
# Then open .env and replace the placeholder with your real key
```

---

## Running the project

```bash
# 1. See today's new stories (free — no AI calls)
python -m src.main --channel news --list

# 2. Test the whole pipeline without paying anything.
#    Shows the most each AI call could cost and saves a placeholder script.
python -m src.main --channel news --dry-run

# 3. Real run: the cheap model picks a story, the writing model writes the script
python -m src.main --channel news

# ...or choose the story yourself (number from --list) and skip the ranking call
python -m src.main --channel news --pick 3

# Run the test suite
pytest
```

Each script is saved to `output/<channel>/<date>-<slug>.md`, with a header (status,
model, word count, estimated length), the script sections, notes for the reviewer, and the
**source title and URL** at the bottom for fact-checking. Every run adds a row to
`output/cost_log.csv` with the tokens used and the estimated cost.

### How Phase 1 works

```
channels/news.yaml ──► RSS feeds ──► dedupe + last 24h ──► skip already-used stories
        │                                                        │
        │                       ┌── --pick N (you choose, free) ─┤
        │                       └── ranking model (Haiku) ───────┘
        ▼                                                        ▼
  prompts.py ──► download + trim article (cached) ──► writing model (Sonnet) ──► output/news/*.md
                                                                                  + cost_log.csv
```

### Keeping costs low

| Guard | Where |
|-------|-------|
| Cheap model for ranking, stronger model only for the final script | `models:` in the channel YAML |
| `max_tokens` cap and `effort` level on every call | `llm:` in the channel YAML |
| Maximum AI calls per run | `llm.max_calls_per_run` |
| Saved AI answers reused for free (`cache/llm/`) | `src/generation/llm.py` |
| Articles downloaded once, HTML stripped, trimmed (`cache/articles/`) | `src/sources/articles.py` |
| Stories that already have a script are skipped | `src/storage/history.py` |
| Prompt caching on the system prompt | `src/generation/llm.py` |
| `--dry-run` makes no paid calls | `src/main.py` |
| Per-run cost log | `output/cost_log.csv` (prices in `config/pricing.yaml`) |

A typical real run makes two calls (one Haiku ranking call and one Sonnet writing call) and
should cost about one to three cents. `--dry-run` shows the worst-case maximum.

---

## Project structure

```
shortsengine/
  channels/         # one YAML config per channel
  config/           # shared settings (model price list)
  src/
    sources/        # RSS and other news fetchers
    generation/     # script writing with Claude
    video/          # voiceover, captions, FFmpeg rendering
    publishing/     # TikTok / Instagram APIs
    storage/        # database models and queries
    main.py         # command-line entry point
  output/           # generated scripts and videos (git-ignored)
  cache/            # cached articles and LLM results (git-ignored)
  tests/
```

---

## Roadmap

| Phase | Status | Description |
|-------|--------|-------------|
| 0 | ✅ Done | Project setup, hello world |
| 1 | 🧪 Built, awaiting first real run | News script generator (text only) |
| 2 | Planned | Video production (TTS + FFmpeg) |
| 3 | Planned | Database and human review queue |
| 4 | Planned | Automated publishing |
| 5 | Planned | Web dashboard |
| 6 | Planned | Analytics loop, more channels |
| 7 | Planned | Cloud deploy, portfolio polish |

---

## Tech stack

- **Language:** Python 3.11
- **AI:** [Anthropic Claude](https://anthropic.com) (Claude Haiku 5.5 for ranking, Claude Sonnet 5.5 for writing)
- **News sources:** RSS via `feedparser`
- **Config:** YAML (one file per channel)
- **Database:** SQLite → Postgres
- **Backend:** FastAPI
- **Video:** FFmpeg + text-to-speech

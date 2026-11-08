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
# Run the pipeline for the news channel
python -m src.main --channel news

# Dry run — no paid API calls (safe for testing)
python -m src.main --channel news --dry-run

# Run the test suite
pytest
```

---

## Project structure

```
shortsengine/
  channels/         # one YAML config per channel
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
| 1 | 🔄 In progress | News script generator (text only) |
| 2 | Planned | Video production (TTS + FFmpeg) |
| 3 | Planned | Database and human review queue |
| 4 | Planned | Automated publishing |
| 5 | Planned | Web dashboard |
| 6 | Planned | Analytics loop, more channels |
| 7 | Planned | Cloud deploy, portfolio polish |

---

## Tech stack

- **Language:** Python 3.11
- **AI:** [Anthropic Claude](https://anthropic.com) (Haiku for ranking, Sonnet for writing)
- **News sources:** RSS via `feedparser`
- **Config:** YAML (one file per channel)
- **Database:** SQLite → Postgres
- **Backend:** FastAPI
- **Video:** FFmpeg + text-to-speech

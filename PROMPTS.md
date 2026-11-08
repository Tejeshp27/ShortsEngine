# ShortsEngine — Claude Code Prompts for Every Phase

How to use this file:
- Paste **one prompt at a time** into Claude Code.
- After each prompt, run what it tells you and do the ✅ check before moving on.
- 🧑 marks steps **you** must do yourself (accounts, keys, approvals). Claude Code can't do them.
- Never paste API keys or passwords into Claude Code. Put them in `.env` yourself.

---

## Checklist — Overall Progress

| Item | Status |
|------|--------|
| Phase 0 — Project setup | ✅ Done |
| 1.1 Fetch the news | ✅ Done |
| 1.2 Connect the AI safely | ✅ Done |
| 1.3 Rank stories | ✅ Done |
| 1.4 Write the script | ✅ Done |
| 1.5 Caching and cleanup | ✅ Done |
| 1.6 Improve the scripts | ⏳ After first week of real runs |
| 2.1 Plan and cost check | ✅ Done (chose edge-tts, Pillow, FFmpeg) |
| 2.2 Voiceover | ✅ Done |
| 2.3 Word-timed captions | ✅ Done |
| 2.4 Visuals | ✅ Done |
| 2.5 Render the video | ✅ Done |
| 2.6 One command + README | ✅ Done |
| 2.7 Improve the videos | ⏳ After posting by hand |
| 3.1 Database design | ✅ Done |
| 3.2 Save everything | ✅ Done |
| 3.3 Review queue | ✅ Done |
| 3.4 Cost report | ✅ Done |
| 4.1 Instagram setup guide | ✅ Done (guide written below) |
| 4.2 Instagram publishing | ✅ Done (code built; 🧑 needs your tokens) |
| 4.3 TikTok setup guide | ✅ Done (guide written below) |
| 4.4 TikTok upload to drafts | ✅ Done (code built; 🧑 needs your tokens) |
| 4.5 Scheduling | ✅ Done |
| 5.1 Web app skeleton | ✅ Done |
| 5.2 Channels page | ✅ Done |
| 5.3 Connect accounts (OAuth) | ✅ Done (code built; 🧑 needs your tokens) |
| 5.4 Review queue in browser | ✅ Done |
| 5.5 Stats dashboard | ✅ Done |
| 6.1 Collect performance data | ✅ Done (code built; 🧑 needs posted videos) |
| 6.2 Analytics page | ✅ Done |
| 6.3 Feedback loop | ✅ Done |
| 6.4 Second channel type (Twitch) | ✅ Done (code built; 🧑 needs Twitch app + permission) |
| 7.1 Deploy | ✅ Done (Docker + guide) |
| 7.2 GitHub Actions CI | ✅ Done |
| 7.3 README for recruiters | ✅ Done |
| 7.4 Resume bullets | ✅ Done |

---

## Prompts you'll use all the time

**Start of every session**
> Read CLAUDE.md and give me a 3-line summary of where the project is and what the next step is.

**Moving to a new phase** (change the number)
> Update CLAUDE.md so "Current phase" says Phase N. Then show me the plan for Phase N as a short checklist before writing any code.

**When something breaks** (paste the error under it)
> This error happened when I ran the command. Explain in simple terms what caused it, then fix it:

**To understand the code** (important for interviews)
> Explain how [file or feature] works step by step, as if I'm a beginner. Then ask me 3 questions to check that I understood it.

**End of every session**
> Run all tests. If they pass, commit and push with a clear message, and update the README if anything changed.

---

## Phase 0 — Project setup ✅ DONE

---

## Phase 1 — News script generator (text only) ✅ DONE

**1.1 Fetch the news** ✅
**1.2 Connect the AI safely** ✅
**1.3 Rank stories** ✅
**1.4 Write the script** ✅
**1.5 Caching and cleanup** ✅

**1.6 Improve the scripts** ⏳ (run after one week of real daily runs)
> Here are scripts that came out weak. Explain what's wrong with each (hook, length, clarity, neutrality), then improve the prompts in prompts.py to fix those problems. Show me before/after for one story.

---

## Phase 2 — Video production ✅ DONE

**2.1 Plan and cost check** ✅ — chose edge-tts (free), Pillow for frames, FFmpeg for encoding
**2.2 Voiceover** ✅
**2.3 Word-timed captions** ✅
**2.4 Visuals** ✅
**2.5 Render the video** ✅
**2.6 One command** ✅

🧑 **Post 1–2 videos a day by hand** for 1–2 weeks and note the views.

**2.7 Improve the videos** ⏳ (run after posting by hand)
> Here are my results so far (paste views/notes). Suggest changes to hooks, length, pacing, caption style, or visuals based on these numbers, then make the ones I approve.

---

## Phase 3 — Database and review queue ✅ DONE

**3.1 Database design** ✅
**3.2 Save everything** ✅
**3.3 Review queue** ✅
**3.4 Cost report** ✅

---

## Phase 4 — Publishing ✅ DONE (code complete; 🧑 tokens required to activate)

**4.1 Instagram setup guide** ✅

### Instagram setup steps (do these yourself):
1. Convert your Instagram account to a **Professional account** (Creator or Business) in the Instagram app → Settings → Account.
2. Connect it to a **Facebook Page**: go to Facebook → Pages → Create, then link it in Instagram Settings → Account → Linked Accounts.
3. Go to [developers.facebook.com](https://developers.facebook.com) → My Apps → Create App → Business type.
4. Add the **Instagram Graph API** product to your app.
5. Under App Review, request these permissions: `instagram_basic`, `instagram_content_publish`, `pages_read_engagement`.
6. Generate a **long-lived User Access Token** (valid ~60 days) in the Graph API Explorer.
7. Add to `.env`:
   ```
   INSTAGRAM_ACCESS_TOKEN=your-token-here
   INSTAGRAM_ACCOUNT_ID=your-ig-account-id
   ```

**4.2 Instagram publishing** ✅ — code in `src/publishing/instagram.py`
**4.3 TikTok setup guide** ✅

### TikTok setup steps (do these yourself):
1. Go to [developers.tiktok.com](https://developers.tiktok.com) → Manage Apps → Create App.
2. Set App Type to **Web**. Add `http://localhost:8000/oauth/tiktok/callback` as a redirect URI.
3. Enable **Login Kit** and **Content Posting API** (request access — approval takes a few days).
4. Note your **Client Key** and **Client Secret**.
5. Add to `.env`:
   ```
   TIKTOK_CLIENT_KEY=your-key
   TIKTOK_CLIENT_SECRET=your-secret
   TIKTOK_ACCESS_TOKEN=your-token-after-oauth
   ```
6. The dashboard's "Connect TikTok" button handles the OAuth flow once your app is approved.

**4.4 TikTok upload to drafts** ✅ — code in `src/publishing/tiktok.py`
**4.5 Scheduling** ✅ — code in `src/publishing/scheduler.py`

---

## Phase 5 — Dashboard and connected accounts ✅ DONE

**5.1 Web app skeleton** ✅ — `src/dashboard/` FastAPI app, run with `python -m src.dashboard.app`
**5.2 Channels page** ✅
**5.3 Connect accounts (OAuth)** ✅ — activate after adding tokens to `.env`
**5.4 Review queue in browser** ✅
**5.5 Stats dashboard** ✅

---

## Phase 6 — Analytics loop and more channels ✅ DONE (code complete)

**6.1 Collect performance data** ✅ — `src/analytics/collector.py`; run after videos are posted
**6.2 Analytics page** ✅
**6.3 Feedback loop** ✅
**6.4 Second channel type (Twitch)** ✅ — `channels/twitch_example.yaml` + `src/sources/twitch.py`

🧑 Create a Twitch developer app at [dev.twitch.tv](https://dev.twitch.tv), add its keys to `.env`, and get written permission from the streamers whose clips you use.

---

## Phase 7 — Portfolio polish ✅ DONE

**7.1 Deploy** ✅ — `Dockerfile` + `docker-compose.yml` + deployment guide in `docs/deploy.md`
**7.2 GitHub Actions CI** ✅ — `.github/workflows/tests.yml`
**7.3 README for recruiters** ✅
**7.4 Resume bullets** ✅ — see `docs/resume.md`

🧑 Record a 1–2 minute demo video of the dashboard and pipeline, and make the repo public when ready.

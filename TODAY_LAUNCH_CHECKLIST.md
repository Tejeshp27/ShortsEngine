<!-- November 18 launch log: verified evidence, owner actions and real publication counts. -->
# Today’s launch — November 18, 2026 (America/Chicago)

**Automation update:** completed a fresh audit before adding runtime work. Mac
launchd supervisor/dashboard services are installed and crash recovery passed.
Official Twitch discovery saved 32 public metadata candidates (five Jason clips),
but no media downloads or creator exports exist. Upload/publication counts below
remain zero. See `AUTOMATION_STATUS.md` and `HUMAN_SETUP_GUIDE.md` for the updated
audit, running-service evidence and the first human-only account step.
Final full test suite: **495 passed**. Clip auto-approval is enabled only after
clean quality gates; public posting remains a dry run until source/account access
is ready. No real clips or uploads have been counted from synthetic tests.

**Next most important step: verify Twitch's viewer-created clip download/share route for Jason's channel.**
The owner does not know Jason and cannot obtain his broadcaster login. The assistant
canceled the waiting `connect jasontheween` process; do not ask for Jason's activation.
Twitch's official viewer clip guide documents downloading clips you create, and its
clip settings document streamer-controlled sharing to viewers' social accounts:
[clip guide](https://help.twitch.tv/s/article/how-to-use-clips?language=en_US),
[sharing settings](https://help.twitch.tv/s/article/clips-settings?language=en_US).
This route uses the owner's Twitch account where the channel permits it. Actual
Jason download/share availability and the permitted reuse scope have not been
verified. Browser inventory reported `browser access host is unavailable`, so the
assistant could not inspect those controls. A fully unattended acquisition path
has not been demonstrated; app credentials alone do not provide one.
The owner selected `jasontheween` and asked the assistant to find the file automatically.
The assistant searched accessible local video folders and OneDrive: no matching footage exists there.
iCloud Drive remains blocked by macOS privacy controls after an approved retry.
Use the automatic Twitch authorization route below, or place a creator-supplied
completed recording in `recordings/twitch/jasontheween/` and the assistant will find it.
Destination account choice is deferred at the owner's request; independent work continues.

**Latest credential check:** saved the supplied Twitch client ID and secret to local
`.env`, restricted that file to its owner, and confirmed Git ignores it. After the
client ID correction, Twitch's official token endpoint returned HTTP 200 and issued
an app token; the official JasonTheWeen channel lookup also returned HTTP 200 and
found the channel. The previous rejection is resolved. No credential/token values
are recorded here. The CLI broadcaster authorization was canceled after the owner
clarified they cannot obtain Jason's login. A successful app login alone
does not authorize footage downloads.

## Verified launch counts

| Result | Count | Evidence |
|---|---:|---|
| Finished creator clips for today | 0 / 3 | No creator recording is present |
| YouTube confirmed uploads | 0 / 3 | No clipping-channel deliveries or posts |
| YouTube confirmed public videos | 0 / 3 | No published URLs |
| TikTok confirmed uploads | 0 / 3 | No clipping-channel deliveries or posts |
| TikTok confirmed public videos | 0 / 3 | No published URLs |

Published URLs: **none**. Synthetic demos, local exports, private uploads, and TikTok
inbox deliveries do not count as confirmed public videos.

## A. Work the assistant can complete automatically

- [x] Read project rules, inventory source/configuration/tests/docs, and inspect existing work without replacing it.
- [x] Run the full existing test suite: **460 passed in 36.79 seconds**.
- [x] Add 16 launch regression cases and rerun the full suite after implementation:
  **476 passed in 34.39 seconds**. `git diff --check` passed; modified Python files
  have header comments and documented functions.
- [x] Check local dependencies: Python 3.15.2, FFmpeg, FFprobe, whisper.cpp, small.en model,
  pytest, httpx, Anthropic, Pydantic, Pillow, FastAPI and token encryption are installed.
- [x] Inspect configuration by presence only; no secrets printed or copied.
- [x] Check the live database read-only: no recordings, clips, uploads or public posts;
  news has YouTube `NewsMadeSimple`, TikTok `newssimplified60`, and Instagram connections.
  `twitch_clips` has no destination account connections.
- [x] Check all eight creator inboxes: empty. Cached recordings/clip outputs are synthetic demos.
- [x] Review acquisition: the corrected Twitch client-ID/secret pair passed live app
  authentication and Jason's official channel lookup. Broadcaster login is waiting;
  Kick app credentials are absent. Supplied authorized recordings bypass both blockers.
- [x] Confirm the existing free pipeline already imports recordings, transcribes locally,
  scores distinct moments, captions and renders vertical drafts, checks media, and supports review.
- [x] Check official platform documentation: manual Studio uploads remain available;
  TikTok API inbox delivery requires the creator to finish posting in TikTok.
- [x] Add and test exact-timestamp CLI cuts using the existing renderer.
- [x] Add and test a manual-upload export folder with MP4s and metadata.
- [x] Run a fresh real-Whisper/render smoke check on original synthetic media (diagnostic only).
  Real whisper.cpp transcribed 70 words; a 20.00-second 1080×1920 H.264/AAC draft passed
  full-file decoding with no quality warnings. Audio mean −18.0 dB, peak −1.5 dB;
  a caption/framing sample was visually checked. No paid calls or uploads occurred.
  Evidence: `output/launch_2026_11_18/smoke/report.json`.
- [x] Independently run automatic local highlight selection → captioned rendering →
  full-file media checks → manual export on the same original synthetic source.
  Evidence: `output/launch_2026_11_18/smoke/automatic_report.json`.
  Both diagnostic databases are isolated from `shortsengine.db`.
- [x] Search Downloads, Movies, Desktop, Documents, Projects, OneDrive and mounted volumes
  for video files/Jason names. No Jason footage found in accessible folders; an unrelated
  personal phone video was excluded. TV app storage and iCloud were unreadable. This is
  not proof that no file exists in protected storage.
- [ ] Import the supplied creator recording and produce three distinct 15–60 second clips.
- [ ] Verify 1080×1920 H.264/AAC, full decoding, audible audio, captions, framing and standalone context.
- [ ] Present the three actual clips and truthful titles for review.
- [ ] Publish approved clips through connected official APIs where available, otherwise prepare Studio uploads.
- [ ] Verify actual platform results and record public URLs here.

## B. Owner actions — in priority order

### 1. Unlock the footage (blocking)

**Existing broadcaster API route — unavailable for this owner; do not prioritize:**

1. **Completed:** app credentials are saved locally and Twitch accepted them. No
   further developer credential setup is needed for this step.
2. **Canceled:** the assistant started
   `python -m src.clips --channel twitch_clips connect jasontheween`, then stopped it
   when the owner clarified they cannot obtain Jason's login. This API route would require authorization through
   [Twitch Activate](https://www.twitch.tv/activate) using his broadcaster account.
   Written reuse permission is already recorded, but it does not supply Twitch API access.
3. Complete when a `jasontheween` login with `channel:manage:clips` is stored and
   the assistant verifies actual official clip downloads. The assistant then finds clips,
   runs local transcription/scoring/rendering, and presents three actual drafts.
4. Twitch also permits authorized editor tokens under `editor:manage:clips`, but the
   existing connect command supports the broadcaster's login. Do not authorize a different
   account while that command is waiting for Jason; it correctly refuses mismatched identities.

**Faster fallback if broadcaster authorization is unavailable:**

1. Use a completed recording supplied by a creator you may repurpose, or your own recording.
2. Put it in the matching local inbox, or send its existing local path:
   `recordings/twitch/kaicenat/`, `recordings/kick/adinross/`,
   `recordings/twitch/stableronaldo/`, `recordings/twitch/adapt/`,
   `recordings/twitch/marlon/`, `recordings/twitch/lacy/`,
   `recordings/twitch/jasontheween/`, or `recordings/twitch/silky/`.
3. Tell the assistant the creator and file path; optional three start/end timestamps.
   Existing YAML records your previous report of consent for these creators; no repeat
   permission request is needed for that same authorized use. Identify permission for a new creator.
4. Complete when the assistant can probe the file, confirm its attribution, and import it.

Where: the project’s local `recordings/` folder. For footage owned by your Twitch account,
use [Twitch Creator Dashboard](https://dashboard.twitch.tv/) and its official clip/video controls;
another creator can supply their export. The [official download API](https://dev.twitch.tv/docs/api/reference/clips/)
requires broadcaster/editor authorization. The [Kick API index](https://docs.kick.com/llms.txt)
does not list a footage-download endpoint; use a creator-supplied file.

### 2. Choose the receiving accounts (blocking publication)

1. Give the intended YouTube channel name/link and TikTok handle when ready.
   The owner has explicitly deferred this selection for now.
2. Sign into those accounts at [YouTube Studio](https://studio.youtube.com/) and
   [TikTok Studio](https://www.tiktok.com/tiktokstudio).
3. Confirm each profile/channel shown is the one intended for stream highlights.
4. Complete when those account identities are confirmed. Existing news connections
   are not assumed to be destinations for stream clips.

### 3. Review the three finished clips (after footage arrives)

1. Open each delivered MP4 and its text metadata. Listen from start to finish.
2. Check spelling/names in captions, readable framing, setup/payoff, creator credit and truthful titles.
3. Tell the assistant which clip IDs/files are approved and any corrections.
4. Complete when all three actual files are approved. Technical checks alone do not approve content.

Where: the exported local folder or [local video library](http://localhost:8000/library).

### 4. Publish today — choose the ready route, without waiting for API approvals

**Manual YouTube:** [YouTube Studio](https://studio.youtube.com/) → Create → Upload videos → select
the three exported MP4s → paste each title/description → answer the audience setting truthfully →
finish Checks → choose Public → Publish. Send the three resulting URLs.
Complete when Studio confirms publication and each URL plays publicly.
[Official Shorts instructions](https://support.google.com/youtube/answer/12779649).

**Manual TikTok:** [TikTok Studio](https://www.tiktok.com/tiktokstudio) → Upload → select one exported
MP4 → paste its caption → inspect preview/privacy → Post. Repeat for all three and send post URLs.
Complete when each appears on the intended profile and its post URL plays.
[Official TikTok Studio overview](https://newsroom.tiktok.com/helping-creators-bring-creativity-to-life-with-tiktok-studio).

**Optional API connection, only if quicker:** open the existing
[clipping channel account page](http://localhost:8000/channels/twitch_clips), click Connect for
YouTube/TikTok, and complete the official Google/TikTok consent flow using the intended account.
Google/TikTok app credentials are already present locally; do not send keys or tokens in chat.
Complete when the clipping channel page shows the correct connected account. Database uniqueness
prevents connecting the same platform account to both news and clips; tell the assistant if the
intended account is an existing news account so its assignment can be handled deliberately.
TikTok requires app approval and user authorization for `video.upload`; an inbox notification
still needs review and Post inside TikTok. If approval blocks this, use Studio immediately.
[Official TikTok inbox upload requirements](https://developers.tiktok.com/docs/en/content-posting-api-get-started-upload-content).

## Current blockers and limits

- **No accessible JasonTheWeen recording or Twitch download login.** This blocks the first real clip.
- **Clipping destination accounts not selected/connected.** Existing connections belong to news.
- Clip API scheduling is paused/dry-run and YouTube privacy is private in YAML. Public
  publication requires the selected accounts, actual clip approval, and explicit public settings.
- Twitch app authentication is verified. The broadcaster login route was canceled;
  investigate official viewer-created clip downloads/sharing instead. Official media
  downloads and unattended acquisition are not yet verified.
  Kick polling credentials do not supply video downloads. These are optional for today.
- `pip check` reports missing `av` and `onnxruntime` for an unused optional faster-whisper install.
  The implemented clipping path uses the installed whisper.cpp, so this is not a launch blocker.
- Docker is not installed. Local rendering works without it; deployment is outside today’s launch path.
- Live clipping-channel OAuth/API uploads are not verified. Never count an attempt or inbox ID as public publication.

## Evidence and next update

Baseline diagnostics: full pytest run and read-only local inventory on November 18.
Final database check: production recordings/clips/deliveries/public-post tables
still contain zero rows; no uploads were attempted. Existing news/account data was preserved.
After footage arrives: import → local Whisper → select three distinct moments → render/verify → review → publish/verify URLs.

Reusable CLI fallback (assistant can run these once footage exists):

```bash
python -m src.clips --channel twitch_clips record /path/authorized-session.mp4 \
  --streamer jasontheween --platform twitch --i-own-this
# Use actual returned video IDs; the numbers below are examples.
python -m src.clips --channel twitch_clips export 12 13 14 --output output/today_uploads
# If YouTube already received them, export only the remaining destination.
python -m src.clips --channel twitch_clips export 12 13 14 \
  --platform tiktok --output output/today_tiktok
```

The exporter rechecks media and permissions, refuses duplicate/overlapping files,
protects existing folders and preserves review state. Requested destinations with
uncertain or completed upload history require reconciliation before another upload.

## Files changed in this launch pass

`src/clips/launch.py`, `src/clips/__main__.py`, `tests/test_clip_launch.py`,
`README.md`, `CLAUDE.md`, `TODAY_LAUNCH_CHECKLIST.md`.

Suggested commit after Claude Code review:
`feat: add launch-day clip cuts and manual upload bundles`.

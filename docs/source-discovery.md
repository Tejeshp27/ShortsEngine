<!-- Public-source discovery setup, operating bounds and manual acquisition instructions. -->
# Twitch source discovery and manual footage imports

Discovery saves public metadata, not video. The source library, persistent job
queue, supervisor and recording importer are shared with the existing pipeline.
No new dependency, paid service or download integration is required.

## Configure Twitch and creators

Create a confidential app in the [Twitch developer console](https://dev.twitch.tv/console/apps)
and set these existing variables in your local, ignored `.env`:

```dotenv
# Twitch app identity used for public Helix metadata.
TWITCH_CLIENT_ID=your-twitch-client-id
# Twitch app secret; never share, log or commit the real value.
TWITCH_CLIENT_SECRET=your-twitch-client-secret
```

The existing client-credentials flow caches the app token. Metadata requests
replace a rejected token once on HTTP 401. HTTP 429 waits until `Ratelimit-Reset`,
with at most two retries; a reset more than two minutes away defers the request
without retrying early. Credentials and provider error bodies are not logged.

Add a creator using the existing Stream clips dashboard or the `streamers` list
in `channels/twitch_clips.yaml`. Keep the existing permission notes accurate:

```yaml
# Creators use the same configuration as existing recording imports.
streamers:
  - username: example_creator       # Twitch login, not display name or URL
    platform: twitch                # twitch or kick
    permission_note: "Written permission received by email" # actual agreement; never invent one
    permission_date: "2026-11-18"   # actual agreement date, not a future date
    enabled: true                   # include this creator in discovery
    recording_directory: "recordings/twitch/example_creator" # optional completed-recording inbox

# Public metadata checks; they follow the existing persisted Start/Stop choice.
discovery:
  enabled: true                     # enable scheduled discovery; manual discovery is also available
  interval_seconds: 21600           # six hours; allowed range 60–604800 seconds
  hours_back: 168                    # clip lookback; allowed range 1–720 hours
  max_pages: 10                      # 1–10 pages, each with first=100, per creator and source type
```

Do not infer permission to use footage from a public discovery or from old creator
configuration notes. Each discovered row starts with `permission_confirmed=false`.
The upload form explicitly confirms permission for the selected file.

## Run discovery

Open [Source videos](http://localhost:8000/clips/twitch_clips/sources) and click
**Check for new sources**, or run:

```bash
source .venv/bin/activate
python -m src.clips --channel twitch_clips discover
python -m src.clips --channel twitch_clips discover --streamer jasontheween
python -m src.clips --channel twitch_clips discover --dry-run
```

Both browser and CLI use the existing `clip_discovery` job and progress page.
Use the library's creator filter and Previous/Next sources links to reach older
discoveries; each page shows up to 100 entries per existing source table. The
upload dropdown contains the discoveries on the current page.
Dry runs make no HTTP requests or database changes to sources. Start automation
on the Stream clips page to enable periodic discovery through the installed
supervisor. `discovery.enabled` and the persisted Start/Stop choice both apply to
scheduled discovery. Manual checks work while scheduling is stopped.

After updating code, restart the existing dashboard and supervisor processes so
they load the new routes and scheduler. Do not reinstall services. On this Mac:

```bash
launchctl kickstart -k "gui/$(id -u)/com.shortsengine.dashboard"
launchctl kickstart -k "gui/$(id -u)/com.shortsengine.clips"
```

The client resolves logins with Get Users, pages through Get Clips with fixed
`started_at`/`ended_at` filters, and pages through Get Videos with `type=archive`.
VODs are listed newest first and are not restricted by the clip date window.
Every query requests `first=100`; cursors are followed until exhausted or bounded
by `max_pages`. Twitch limits a clip query to approximately 1,000 results, so this
is a bounded recent-source catalog, not a guaranteed historical archive. A page
limit warning appears on the job page when results are partial.

Public fields are stored in the existing `clips` table: source type, external ID,
creator login/display name, credit link, clipper name, source URL, title, creation
time, duration in seconds, views, thumbnail URL, game ID and available VOD offset
and parent-video ID. Fields absent from an official response remain empty or null;
VOD responses do not supply a clipper or game ID. Thumbnails are stored as URLs,
not downloaded. Identity is unique on `(platform, external_id)`, matching the
existing store's global duplicate policy across channels. A source already owned
by a different channel is reported rather than reassigned. Existing finder rows
are migrated in place. Repeat discovery refreshes metadata, including views,
without clearing permission, file attachments or acquired/processed state.

Kick exposes an explicit empty discovery interface with an unsupported notice;
no clip/VOD endpoint or guessed playback route is called. Its existing live-status
monitor and authorized recording uploads remain available.

## Download Landscape Version → upload form

1. Open the entry's **View official source** link. Sign into your own Twitch account.
2. For clips Twitch allows your account to export, use its official **Download
   Landscape Version** (landscape download) control. Availability depends on the
   account and clip; this is not promised for arbitrary discovered clips. Twitch
   describes downloads for clips of your channel and clips you created in its
   [Clips help](https://help.twitch.tv/s/article/how-to-use-clips?language=en_US).
3. Return to **Source videos → Add an authorized video**. Select that entry under
   **Existing discovery**, choose its creator/platform, select the MP4, confirm
   the file is complete and permitted, and click **Store source video**.
4. The importer checks the file, fingerprints and stores the original, then
   attaches its internal recording ID to the original discovery. That entry
   becomes `acquired` with `permission_confirmed=true`. The internal recording
   is hidden as a separate library card. Its source ID, public URL and credits
   stay unchanged. Reuploading identical bytes to the same entry is idempotent.
5. Play the original with audio and seeking in the source library. Uploading
   does not start transcription, editing or posting. Keep `acquisition.only: true`.

For a VOD, supply an authorized completed recording/export; the clip landscape
control is not a general VOD-download route. Choose **Unlisted recording** only
when the file has no corresponding discovered entry. Filename guessing is never
used to associate media with a public source. The explicit selection and permission
checkbox are user attestations; the importer cannot prove a file depicts that URL.

## What is still needed for fully automatic footage

Discovery can run unattended with only the app credentials and an awake, online
Mac. Media acquisition still needs a permitted delivery source: for example, an
authorized recorder/export producer writing completed files to the configured
creator inbox, or a documented provider feed with actual file access. Supply the
provider/interface and how access is granted; never paste secret tokens into chat.
The existing inbox scanner can already import completed authorized files.

A public clip/VOD URL or viewer sign-in is not a file-delivery API. This discovery
job never calls Get Clips Download, follows thumbnail/media URLs, scrapes playback,
or downloads video. Editing and destination-account setup remain separate steps.

Official references: [Get Clips](https://dev.twitch.tv/docs/api/reference/clips/),
[Get Videos](https://dev.twitch.tv/docs/api/reference/videos/),
[app tokens](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/), and
[pagination/rate limits](https://dev.twitch.tv/docs/api/guide/).

## Verification and implementation files

The full offline suite covers token fetch/refresh, login resolution, clip/VOD
pagination, rate-limit waits and bounded retries, complete provenance, repeat-run
identity/view refresh, upload attachment, scheduler timing, private job controls
and library pagination. Actual file validation/playback also retains the existing
FFmpeg-generated original-media integration test.
Final result: **545 passed in 38.37 seconds** (31 added; 514 baseline).

After reloading the installed services on November 18, 2026, the supervisor's real
job `8e67dcd8a30644c59d968047639aee8a` succeeded with 5,286 new and 535 updated
metadata items and no source errors. The database then held **5,358 discovered
sources, including 369 VODs, with zero attached video files or imported creator
recordings**. Counts include pre-existing metadata; the job's updated count can
include entries repeated by Twitch across pages. Page-limit warnings were reported
for five creators; Kick reported its expected unsupported notice. These are public
metadata records, not acquired footage. Offline test requests remain fully mocked.

Files changed for Claude Code review:

- `src/sources/twitch.py`, `src/sources/kick.py`.
- `src/clips/discovery.py`, `src/clips/store.py`, `src/clips/schema.py`,
  `src/clips/settings.py`, `src/clips/acquisition.py`, `src/clips/automation.py`,
  `src/clips/__main__.py`.
- `src/dashboard/clip_routes.py`, `src/dashboard/jobs.py`,
  `src/dashboard/worker.py`, `src/dashboard/templates/clip_sources.html`,
  `src/dashboard/templates/job.html`.
- `channels/twitch_clips.yaml`, `.env.example`, `tests/test_source_discovery.py`.
- `README.md`, `CLAUDE.md`, `AUTOMATION_STATUS.md`,
  `docs/source-discovery.md` (the acquisition plan it mentions was completed by the live recorder and removed 2026-11-20).

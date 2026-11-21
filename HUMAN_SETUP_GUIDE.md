<!-- Human-only account/source decisions. The assistant performs configuration and terminal work. -->
# Human setup guide

**Current phase supersedes the older sequence below:** get authorized raw footage
into the site before editing or posting. The [source library](http://localhost:8000/clips/twitch_clips/sources)
now accepts completed source videos from either platform directly in the browser,
stores originals and provides private playback. This is a fallback import, not
fully automatic remote acquisition. No path copying or manual folder movement is
required. The next acquisition milestone is connecting an actual permitted media
delivery source; this is now the [rolling live recorder](docs/live-recording.md). No further browser
connection troubleshooting or publishing setup is required to review step 1.

**Do only the first action now.** The background services are installed, running,
and recover automatically from a process crash. You do not need to start Terminal,
install packages, move files around, create tables or restart the app each day.
App credentials for Twitch, Google and TikTok are already stored locally.

## First action: your own Twitch sign-in for viewer-side source access

1. **What:** sign into your own Twitch account, not Jason's. You do not need to know
   him or ask him to use an activation code. His broadcaster-only login was canceled.
2. **Why:** the implemented download API requires broadcaster/editor authorization,
   which this project does not have for Jason. Twitch also documents viewer-created
   clip downloads and streamer-controlled social sharing through its website. We
   must check your actual eligible controls and permitted reuse before using that
   route. App credentials alone do not grant video-download rights.
3. **Exact official website:** [Twitch login](https://www.twitch.tv/login), then
   [Jason's channel](https://www.twitch.tv/jasontheween).
4. **Clicks:** click **Log In**, enter your own username/password, and finish any
   two-factor prompt yourself. Confirm the avatar/account menu shows your account.
   Do not use the expired broadcaster activation link from earlier in this chat.
5. **Information:** your own Twitch account credentials go only into Twitch's login
   page. Do not put passwords, recovery codes, cookies or tokens into chat.
6. **What you receive:** a signed-in viewer session. This is not a broadcaster grant
   and does not establish unrestricted permission to republish a whole channel.
7. **What to do with it:** tell the assistant you're signed in and which browser
   you're using. No file paths, coding or secret copying are required. If a public
   creator policy/license is the basis for the existing reported permission, its
   public URL is useful; do not assume a third-party campaign listing is a license.
8. **Verify:** your own profile menu opens without a login prompt. The assistant
   must separately verify the download/share controls; a logged-in session alone
   does not mean footage acquisition works.
9. **Next:** the assistant checks whether connected browser tools can access that
   session and whether Jason's channel permits the required viewer workflow. If
   browser access remains unavailable, that is an explicit tool-access blocker.
   No manual daily-download routine or unattended website bot is being promised.
   Once permitted media is actually accessible, the existing worker can generate
   and validate clips automatically. A supported continuous source is still
   needed for fully unattended daily acquisition.

Official references checked for this guide:
[create/edit/share Twitch clips](https://help.twitch.tv/s/article/how-to-use-clips?language=en_US),
[streamer-controlled clip sharing](https://help.twitch.tv/s/article/clips-settings?language=en_US),
[restricted download API](https://dev.twitch.tv/docs/api/reference/clips/).

The assistant already discovered five public Jason clip candidates through the
official metadata API. They are not downloaded or reviewed. Examples for checking
eligible viewer controls, not approved publication content:

- [Candidate 1 — 20.5 seconds](https://www.twitch.tv/jasontheween/clip/FurryAntsyRamenJonCarnage-ZcZa5r607qBCjERF)
- [Candidate 2 — 58.1 seconds](https://www.twitch.tv/jasontheween/clip/GrossSmoggyLadiesFUNgineer-ke4af90JEX91EDuX)
- [Candidate 3 — 30 seconds](https://www.twitch.tv/jasontheween/clip/ManlyProudPidgeonBloodTrail-PjN9TVhQ1zqg3GPr)

## Later: connect the selected YouTube account

The owner deferred destination selection earlier. This remains queued; do not
connect the existing news account to clips by assumption.

1. **What:** select and authorize the YouTube channel for stream highlights.
2. **Why:** Google app credentials exist, but `twitch_clips` has no YouTube account.
3. **Sites:** [local clipping channel](http://localhost:8000/channels/twitch_clips),
   then Google's official sign-in/consent page reached by the connection button.
4. **Clicks:** open the local page, sign into the existing dashboard if prompted,
   find YouTube, click **Connect**, select the intended Google account/channel,
   inspect the requested upload/read permissions, and continue if correct. The
   button's action starts the existing OAuth flow; the assistant handles its config.
5. **Information:** the target channel's name/link; enter Google credentials only
   on Google's page. No new API key is requested.
6. **Receive:** return to the local dashboard with the channel's connected name.
7. **Store:** the app encrypts access/refresh credentials automatically; paste none
   in chat. If Google rejects a redirect, the assistant checks the configured local
   callback and prepares the exact correction. Only console ownership/consent is
   a human task. [Official OAuth client controls](https://support.google.com/cloud/answer/15549257?hl=en).
8. **Verify:** the clipping channel page shows the correct YouTube account; the
   assistant verifies identity/scopes and an actual authorized upload afterward.
9. **Next:** the assistant configures public privacy/schedule for that selected
   destination, checks three actual clips and uploads only authorized media.
   Private uploads, processing videos and simulated tests are not public success.

Production note: OAuth testing status can require renewed sign-in; long-term
Google OAuth verification/publishing status is controlled in the project owner's
Cloud Console. The assistant cannot promise indefinite refresh credentials.
[Google production policy](https://developers.google.com/identity/protocols/oauth2/production-readiness/policy-compliance).

## Later: TikTok account and approved inbox access

1. **What:** connect the intended TikTok account for the permitted inbox workflow.
2. **Why:** clips has no account; the news connection is not shared implicitly.
3. **Sites:** [local clipping channel](http://localhost:8000/channels/twitch_clips),
   [TikTok developer apps](https://developers.tiktok.com/apps/),
   [TikTok Studio](https://www.tiktok.com/tiktokstudio).
4. **Clicks:** on the local channel page click TikTok's **Connect**, sign into the
   intended TikTok account, inspect consent, and authorize the requested approved
   scopes. If TikTok rejects `video.upload`, open the existing app in the developer
   portal, open its products/scopes configuration, and request Content Posting API
   upload access. Portal labels can vary by app state; the assistant must inspect
   the actual screen before giving a more specific click path.
5. **Information:** actual app purpose, URLs and an honest demonstration of the
   implemented flow if review asks for them. Do not claim it is a public creator
   product or audited Direct Post app when it is not. The assistant can prepare
   descriptions and evidence; account-owner submissions/agreements remain yours.
6. **Receive:** approved `video.upload` plus user OAuth authorization; approval
   cannot be guaranteed or substituted by code.
7. **Store:** let the local callback encrypt tokens and handle refresh rotation.
8. **Verify:** correct account on the clips page, followed by actual API status
   `SEND_TO_USER_INBOX` after a real upload. That is not a published TikTok.
9. **Next:** the assistant queues/transfers eligible media, tracks its state, and
   supplies text. You complete Post from the TikTok inbox or official Studio.

**Unattended public Direct Post is not promised.** Current guidelines disallow
private account-management utilities and arbitrary cross-platform copying, and
require creator posting controls. This project's present form does not meet that
intended-use model. An audit request is not a workaround; compliant product scope
and platform acceptance would be needed before implementing that mode. No browser
posting automation or unofficial upload endpoint is used to bypass this limit.

[Official upload requirements](https://developers.tiktok.com/docs/en/content-posting-api-get-started-upload-content),
[Direct Post rules and audit link](https://developers.tiktok.com/docs/en/content-sharing-guidelines),
[app review](https://developers.tiktok.com/docs/en/app-review-guidelines).

## Optional: Kick monitoring or an always-on host

Kick is not today's first blocker. Its app credentials enable status polling,
not another creator's unrestricted VOD downloads. If you own the recording,
the assistant can configure its completed-output location once the source is
identified. Owner-only download UI: [Creator Dashboard → Studio → VODs](https://dashboard.kick.com/studio/vods)
→ choose the VOD → **Download** → choose quality. This is a manual fallback,
not a newly automated acquisition integration.
[Official download limits](https://help.kick.com/en/articles/7832538-how-to-download-your-kick-vod).

No server purchase is required for the installed Mac services. They run while
the Mac is awake, powered, connected and logged in. For service through sleep,
logout or power-off, a separate always-on host is needed. The existing Docker
deployment plus new runtime service is prepared; its container build is unverified
on this Mac because Docker is absent. Only if you choose cloud deployment will
the assistant request a provider/account choice, machine/billing approval and
host access. It will do the deployment work after that, without provisioning
paid resources first. No cloud approval is requested now.

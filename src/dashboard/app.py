# =============================================================
# src/dashboard/app.py — FastAPI web dashboard
#
# Runs a local web app so you can manage ShortsEngine from
# a browser. Covers Phase 5:
#   - /           → stats overview (videos made, cost, status)
#   - /channels   → list channels
#   - /videos     → review queue (approve / reject in browser)
#   - /video/<id> → detail view for one video
#   - /publish    → trigger scheduler manually
#
# Run with:
#   python -m src.dashboard.app
# Then open http://localhost:8000 in your browser.
#
# Authentication: a simple password set in .env (DASHBOARD_PASSWORD).
# In Phase 7 this would be replaced with a proper login system.
# =============================================================

import os

from fastapi import FastAPI, Form, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
import uvicorn

from src.storage.database import create_tables
from src.storage.queries import (
    approve_video,
    get_all_videos,
    get_video_by_id,
    get_videos_by_status,
    reject_video,
)
from src.analytics.collector import get_metrics_summary

app = FastAPI(title="ShortsEngine Dashboard")
security = HTTPBasic()


def _check_auth(credentials: HTTPBasicCredentials = Depends(security)):
    """
    Verify the dashboard password using HTTP Basic Auth.

    The password is set via DASHBOARD_PASSWORD in .env.
    If not set, any password is accepted (dev mode only).

    Args:
        credentials: The username/password from the browser prompt.

    Returns:
        str: The username (not used, but FastAPI requires a return value).

    Raises:
        HTTPException 401: If the password is wrong.
    """
    expected_password = os.getenv("DASHBOARD_PASSWORD", "")
    if expected_password:
        correct = secrets.compare_digest(
            credentials.password.encode("utf-8"),
            expected_password.encode("utf-8"),
        )
        if not correct:
            raise HTTPException(
                status_code=401,
                detail="Wrong password",
                headers={"WWW-Authenticate": "Basic"},
            )
    return credentials.username


# ---------------------------------------------------------------------------
# Simple HTML helpers (no Jinja2 needed for this phase)
# ---------------------------------------------------------------------------

def _page(title, body, nav=True):
    """
    Wrap body HTML in a full page with a nav bar and basic styling.

    Args:
        title (str): Page title shown in the browser tab.
        body (str): HTML content for the page body.
        nav (bool): Whether to show the top nav bar.

    Returns:
        str: Full HTML page as a string.
    """
    nav_html = ""
    if nav:
        nav_html = """
        <nav>
          <a href="/">Dashboard</a>
          <a href="/channels">Channels</a>
          <a href="/videos">Review Queue</a>
          <a href="/videos?status=approved">Approved</a>
          <a href="/videos?status=posted">Posted</a>
          <a href="/analytics">Analytics</a>
        </nav>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — ShortsEngine</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #0f0f1a; color: #e8e8f0; line-height: 1.6; }}
    nav {{ background: #1a1a2e; padding: 1rem 2rem; display: flex; gap: 1.5rem;
           border-bottom: 1px solid #2a2a4e; }}
    nav a {{ color: #8888cc; text-decoration: none; font-weight: 500; }}
    nav a:hover {{ color: #fff; }}
    .container {{ max-width: 1100px; margin: 0 auto; padding: 2rem; }}
    h1 {{ font-size: 1.8rem; margin-bottom: 1.5rem; color: #fff; }}
    h2 {{ font-size: 1.2rem; margin: 1.5rem 0 0.75rem; color: #aaa; text-transform: uppercase;
          letter-spacing: 0.05em; font-size: 0.85rem; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 2rem; }}
    .card {{ background: #1a1a2e; border: 1px solid #2a2a4e; border-radius: 8px; padding: 1.25rem; }}
    .card-value {{ font-size: 2rem; font-weight: 700; color: #8888ff; }}
    .card-label {{ font-size: 0.85rem; color: #888; margin-top: 0.25rem; }}
    table {{ width: 100%; border-collapse: collapse; background: #1a1a2e;
             border-radius: 8px; overflow: hidden; }}
    th {{ background: #2a2a4e; padding: 0.75rem 1rem; text-align: left; font-size: 0.85rem;
          color: #aaa; text-transform: uppercase; letter-spacing: 0.05em; }}
    td {{ padding: 0.75rem 1rem; border-bottom: 1px solid #2a2a4e; font-size: 0.9rem; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: #22223a; }}
    .badge {{ display: inline-block; padding: 0.2rem 0.6rem; border-radius: 999px; font-size: 0.75rem; font-weight: 600; }}
    .badge-drafted {{ background: #333; color: #aaa; }}
    .badge-approved {{ background: #1a4a1a; color: #6f6; }}
    .badge-posted {{ background: #1a2a4a; color: #6af; }}
    .badge-failed {{ background: #4a1a1a; color: #f66; }}
    .btn {{ display: inline-block; padding: 0.4rem 1rem; border-radius: 6px;
            text-decoration: none; font-size: 0.85rem; font-weight: 600;
            border: none; cursor: pointer; }}
    .btn-green {{ background: #1a4a1a; color: #6f6; }}
    .btn-red {{ background: #4a1a1a; color: #f66; }}
    .btn-blue {{ background: #1a2a4a; color: #6af; }}
    form {{ display: inline; }}
    .script-box {{ background: #111; border: 1px solid #2a2a4e; border-radius: 6px;
                   padding: 1rem; white-space: pre-wrap; font-family: monospace;
                   font-size: 0.85rem; line-height: 1.5; color: #ccc;
                   max-height: 500px; overflow-y: auto; margin-top: 1rem; }}
    input[type=text], textarea {{ background: #111; border: 1px solid #2a2a4e; color: #eee;
                                   border-radius: 6px; padding: 0.5rem 0.75rem; width: 100%;
                                   font-size: 0.9rem; }}
    label {{ font-size: 0.85rem; color: #888; display: block; margin-bottom: 0.3rem; }}
    .form-group {{ margin-bottom: 1rem; }}
    .meta {{ font-size: 0.8rem; color: #666; }}
  </style>
</head>
<body>
  {nav_html}
  <div class="container">
    {body}
  </div>
</body>
</html>"""


def _status_badge(status):
    return f'<span class="badge badge-{status}">{status}</span>'


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.on_event("startup")
def startup():
    """Create database tables when the app starts, if they don't exist."""
    create_tables()


@app.get("/", response_class=HTMLResponse)
def dashboard_home(username: str = Depends(_check_auth)):
    """
    Main dashboard: stats cards + recent videos table.
    """
    all_videos = get_all_videos()

    drafted = sum(1 for v in all_videos if v["status"] == "drafted")
    approved = sum(1 for v in all_videos if v["status"] == "approved")
    posted = sum(1 for v in all_videos if v["status"] == "posted")
    total_cost = sum(v["total_cost_usd"] for v in all_videos)

    cards_html = f"""
    <div class="cards">
      <div class="card">
        <div class="card-value">{len(all_videos)}</div>
        <div class="card-label">Total videos</div>
      </div>
      <div class="card">
        <div class="card-value">{drafted}</div>
        <div class="card-label">Awaiting review</div>
      </div>
      <div class="card">
        <div class="card-value">{approved}</div>
        <div class="card-label">Approved</div>
      </div>
      <div class="card">
        <div class="card-value">{posted}</div>
        <div class="card-label">Posted</div>
      </div>
      <div class="card">
        <div class="card-value">${total_cost:.3f}</div>
        <div class="card-label">Total AI cost</div>
      </div>
    </div>"""

    rows = ""
    for v in all_videos[:20]:
        rows += f"""<tr>
          <td><a href="/video/{v['id']}" style="color:#8888ff">{v['story_title'][:60]}</a></td>
          <td>{v['channel']}</td>
          <td>{_status_badge(v['status'])}</td>
          <td>${v['total_cost_usd']:.4f}</td>
          <td class="meta">{v['created_at'][:10]}</td>
        </tr>"""

    table_html = f"""
    <table>
      <thead><tr><th>Story</th><th>Channel</th><th>Status</th><th>Cost</th><th>Date</th></tr></thead>
      <tbody>{rows or '<tr><td colspan="5" style="color:#666;text-align:center">No videos yet</td></tr>'}</tbody>
    </table>"""

    body = f"<h1>ShortsEngine Dashboard</h1>{cards_html}<h2>Recent videos</h2>{table_html}"
    return HTMLResponse(_page("Dashboard", body))


@app.get("/channels", response_class=HTMLResponse)
def channels_page(username: str = Depends(_check_auth)):
    """
    Lists every channel config found in the channels/ folder.
    """
    import yaml as pyyaml
    channels = []
    folder = "channels"
    if os.path.isdir(folder):
        for filename in sorted(os.listdir(folder)):
            if filename.endswith(".yaml"):
                with open(os.path.join(folder, filename)) as f:
                    cfg = pyyaml.safe_load(f)
                channels.append((filename.replace(".yaml", ""), cfg))

    rows = ""
    for key, cfg in channels:
        rows += f"""<tr>
          <td>{key}</td>
          <td>{cfg.get('name', key)}</td>
          <td>{cfg.get('niche', '—')}</td>
          <td>{', '.join(cfg.get('platforms', []))}</td>
          <td>{cfg.get('models', {}).get('writing', '—')}</td>
        </tr>"""

    table = f"""<table>
      <thead><tr><th>Key</th><th>Name</th><th>Niche</th><th>Platforms</th><th>Writing model</th></tr></thead>
      <tbody>{rows or '<tr><td colspan="5" style="color:#666">No channels found</td></tr>'}</tbody>
    </table>
    <p style="margin-top:1rem;color:#666;font-size:0.85rem">
      To add a channel, create a new YAML file in the channels/ folder.
    </p>"""

    body = f"<h1>Channels</h1>{table}"
    return HTMLResponse(_page("Channels", body))


@app.get("/videos", response_class=HTMLResponse)
def videos_page(status: str = "drafted", username: str = Depends(_check_auth)):
    """
    Review queue: shows videos with the given status.
    """
    videos = get_videos_by_status(status)

    rows = ""
    for v in videos:
        action_buttons = ""
        if v["status"] == "drafted":
            action_buttons = f"""
              <form method="post" action="/video/{v['id']}/approve">
                <button class="btn btn-green" type="submit">Approve</button>
              </form>
              <form method="post" action="/video/{v['id']}/reject">
                <button class="btn btn-red" type="submit">Reject</button>
              </form>"""
        rows += f"""<tr>
          <td><a href="/video/{v['id']}" style="color:#8888ff">{v['story_title'][:55]}</a></td>
          <td>{v['channel']}</td>
          <td>{_status_badge(v['status'])}</td>
          <td>${v['total_cost_usd']:.4f}</td>
          <td>{action_buttons}</td>
        </tr>"""

    table = f"""<table>
      <thead><tr><th>Story</th><th>Channel</th><th>Status</th><th>Cost</th><th>Actions</th></tr></thead>
      <tbody>{rows or '<tr><td colspan="5" style="color:#666;text-align:center">No videos with status "{status}"</td></tr>'}</tbody>
    </table>"""

    filter_links = " | ".join(
        f'<a href="/videos?status={s}" style="color:#8888cc">{s}</a>'
        for s in ["drafted", "approved", "posted", "failed"]
    )
    body = f"<h1>Review Queue</h1><p style='margin-bottom:1rem;color:#666;font-size:0.85rem'>{filter_links}</p>{table}"
    return HTMLResponse(_page("Review Queue", body))


@app.get("/video/{video_id}", response_class=HTMLResponse)
def video_detail(video_id: int, username: str = Depends(_check_auth)):
    """
    Detail view for one video: shows the script and approve/reject buttons.
    """
    video = get_video_by_id(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    script_html = ""
    if video["script_path"] and os.path.exists(video["script_path"]):
        with open(video["script_path"], "r", encoding="utf-8") as f:
            script_content = f.read()
        script_html = f'<div class="script-box">{script_content}</div>'
    else:
        script_html = '<p style="color:#666">Script file not found.</p>'

    video_html = ""
    if video["video_path"] and os.path.exists(video["video_path"]):
        video_html = f'<p style="margin:1rem 0;color:#8888cc">Video: {video["video_path"]}</p>'

    action_html = ""
    if video["status"] == "drafted":
        action_html = f"""
        <div style="display:flex;gap:1rem;margin:1rem 0">
          <form method="post" action="/video/{video_id}/approve">
            <div class="form-group">
              <label>Reviewer notes (optional)</label>
              <input type="text" name="notes" placeholder="Looks good!">
            </div>
            <button class="btn btn-green" type="submit">✓ Approve</button>
          </form>
          <form method="post" action="/video/{video_id}/reject">
            <div class="form-group">
              <label>Reason for rejection (optional)</label>
              <input type="text" name="notes" placeholder="Inaccurate...">
            </div>
            <button class="btn btn-red" type="submit">✗ Reject</button>
          </form>
        </div>"""

    notes_html = ""
    if video["reviewer_notes"]:
        notes_html = f'<p style="color:#888;font-size:0.85rem">Notes: {video["reviewer_notes"]}</p>'

    body = f"""
    <h1>{video['story_title']}</h1>
    <p class="meta" style="margin-bottom:1rem">
      Channel: {video['channel']} &nbsp;|&nbsp;
      Status: {_status_badge(video['status'])} &nbsp;|&nbsp;
      Cost: ${video['total_cost_usd']:.4f} &nbsp;|&nbsp;
      Created: {video['created_at'][:10]}
    </p>
    <p><a href="{video['story_url']}" target="_blank" style="color:#8888cc">Source article ↗</a></p>
    {notes_html}
    {action_html}
    {video_html}
    <h2>Script</h2>
    {script_html}
    """
    return HTMLResponse(_page(video["story_title"][:40], body))


@app.post("/video/{video_id}/approve")
def approve(video_id: int, notes: str = Form(default=""),
            username: str = Depends(_check_auth)):
    """Approve a drafted video and redirect back to the review queue."""
    video = get_video_by_id(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    approve_video(video_id, notes=notes)
    return RedirectResponse(url="/videos", status_code=303)


@app.post("/video/{video_id}/reject")
def reject(video_id: int, notes: str = Form(default=""),
           username: str = Depends(_check_auth)):
    """Reject a drafted video and redirect back to the review queue."""
    video = get_video_by_id(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    reject_video(video_id, notes=notes)
    return RedirectResponse(url="/videos", status_code=303)


@app.get("/analytics", response_class=HTMLResponse)
def analytics_page(channel: str = None, username: str = Depends(_check_auth)):
    """
    Analytics page: shows views, likes, comments and shares per posted video.

    If the channel query param is provided, only that channel's videos are shown.
    Example: /analytics?channel=news
    """
    rows = get_metrics_summary(channel=channel)

    # Build a simple bar chart using HTML/CSS (no JS library needed).
    max_views = max((r["total_views"] or 0 for r in rows), default=1) or 1

    table_rows = ""
    for r in rows:
        views = r["total_views"] or 0
        bar_width = max(1, int((views / max_views) * 200))
        bar_html = (
            f'<div style="display:inline-block;height:12px;width:{bar_width}px;'
            f'background:#8888ff;border-radius:2px;vertical-align:middle"></div>'
            f' {views:,}'
        )
        table_rows += f"""<tr>
          <td><a href="/video/{r['id']}" style="color:#8888ff">{r['story_title'][:55]}</a></td>
          <td>{r['channel']}</td>
          <td>{bar_html}</td>
          <td>{r['total_likes'] or 0:,}</td>
          <td>{r['total_comments'] or 0:,}</td>
          <td>{r['total_shares'] or 0:,}</td>
          <td class="meta">{(r['created_at'] or '')[:10]}</td>
        </tr>"""

    table = f"""<table>
      <thead>
        <tr>
          <th>Story</th><th>Channel</th><th>Views</th>
          <th>Likes</th><th>Comments</th><th>Shares</th><th>Date</th>
        </tr>
      </thead>
      <tbody>
        {table_rows or '<tr><td colspan="7" style="color:#666;text-align:center">No posted videos with metrics yet — post videos and run the collector.</td></tr>'}
      </tbody>
    </table>"""

    note = (
        '<p style="color:#666;font-size:0.85rem;margin-top:1rem">'
        "Run <code>python -m src.analytics.collector</code> daily to collect fresh metrics.</p>"
    )

    body = f"<h1>Analytics</h1>{table}{note}"
    return HTMLResponse(_page("Analytics", body))


if __name__ == "__main__":
    uvicorn.run("src.dashboard.app:app", host="0.0.0.0", port=8000, reload=True)

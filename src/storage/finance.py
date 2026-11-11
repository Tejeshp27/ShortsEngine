# =============================================================
# Shared money ledger and paid-call tracking for the CLI and UI.
# New calls are saved before contacting a paid service, so a crash
# leaves a visible uncertain estimate instead of hiding spending.
# Legacy video totals remain included without counting calls twice.
# =============================================================

import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from src.storage.database import get_connection

LEDGER_SQL = """
    SELECT channel, CASE WHEN length(created_at) = 19 THEN date(created_at, 'utc')
                   ELSE date(created_at) END AS day, cost_usd AS expense,
           0.0 AS income, purpose AS note, state, 'api' AS kind, id
    FROM cost_events
    UNION ALL
    SELECT v.channel, CASE WHEN length(v.created_at) = 19 THEN date(v.created_at, 'utc')
                     ELSE date(v.created_at) END,
           MAX(0, v.total_cost_usd - COALESCE((
               SELECT SUM(c.cost_usd) FROM cost_events c WHERE c.video_id = v.id
           ), 0)), 0.0, 'Earlier video costs', 'legacy', 'legacy', v.id
    FROM videos v
    UNION ALL
    SELECT channel, entry_date,
           CASE WHEN kind = 'expense' THEN amount_cents / 100.0 ELSE 0 END,
           CASE WHEN kind = 'income' THEN amount_cents / 100.0 ELSE 0 END,
           note, 'recorded', kind, id
    FROM money_entries
"""


def utc_now():
    """Return the current UTC time as sortable text for new database records."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ledger(channel=None):
    """Return income and expenses, newest first, optionally for channel."""
    connection = get_connection()
    try:
        query = f"SELECT * FROM ({LEDGER_SQL}) WHERE (expense > 0 OR income > 0)"
        values = []
        if channel:
            query += " AND channel = ?"
            values.append(channel)
        query += " ORDER BY day DESC, id DESC"
        rows = []
        for row in connection.execute(query, values):
            rows.append(dict(row))
        return rows
    finally:
        connection.close()


def money_summary(channel=None):
    """Return totals, monthly totals and entries for channel or all channels."""
    entries = ledger(channel)
    totals = {"income": 0.0, "expense": 0.0, "profit": 0.0}
    months = {}
    for entry in entries:
        month = entry["day"][:7]
        if month not in months:
            months[month] = {"month": month, "income": 0.0, "expense": 0.0, "profit": 0.0}
        for field in ("income", "expense"):
            totals[field] += entry[field]
            months[month][field] += entry[field]
        months[month]["profit"] = months[month]["income"] - months[month]["expense"]
    totals["profit"] = totals["income"] - totals["expense"]
    return {"totals": totals, "months": list(months.values()), "entries": entries}


def add_money_entry(channel, platform, kind, amount, entry_date, note, submission_id):
    """Save a manual payout or expense and return its ID.

    channel/platform identify the account; kind is income or expense. amount is
    dollar text, entry_date is YYYY-MM-DD, note is optional, and submission_id
    prevents a double-click from saving the same form twice.
    """
    if kind not in ("income", "expense") or platform not in ("tiktok", "instagram", "other"):
        raise ValueError("Choose a valid entry type and platform.")
    try:
        dollars = Decimal(amount)
        if not dollars.is_finite() or dollars <= 0 or dollars > 1_000_000:
            raise ValueError("Enter an amount above zero and below $1,000,000.")
        if dollars != dollars.quantize(Decimal("0.01")):
            raise ValueError("Use at most two decimal places for dollars.")
        cents = int(dollars * 100)
        parsed_date = datetime.strptime(entry_date, "%Y-%m-%d").date()
        if parsed_date.isoformat() != entry_date:
            raise ValueError("Dates must use YYYY-MM-DD.")
    except (InvalidOperation, ValueError) as error:
        raise ValueError("Enter a valid date and positive dollar amount with at most two decimals.") from error
    if len(note) > 1000 or not submission_id or len(submission_id) > 100:
        raise ValueError("Reload the form and keep the note under 1,000 characters.")
    connection = get_connection()
    try:
        connection.execute("""
            INSERT OR IGNORE INTO money_entries
                (channel, platform, kind, amount_cents, entry_date, note, submission_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (channel, platform, kind, cents, entry_date, note, submission_id, utc_now()))
        row = connection.execute("SELECT id FROM money_entries WHERE submission_id = ?",
                                 (submission_id,)).fetchone()
        connection.commit()
        return row["id"]
    finally:
        connection.close()


def spent_today(connection):
    """Return today's recorded dollar spending using the open connection."""
    row = connection.execute(
        f"SELECT COALESCE(SUM(expense), 0) AS total FROM ({LEDGER_SQL}) WHERE day = ?",
        (utc_now()[:10],),
    ).fetchone()
    return row["total"]


class CostTracker:
    """Save spending for one run and enforce its optional dollar limit.

    channel identifies the channel; video_id and job_id link work to its result.
    budget is the run's maximum estimate, or None for a normal CLI run.
    """

    def __init__(self, channel, video_id=None, job_id=None, budget=None):
        """Remember channel, optional video/job IDs and budget; return nothing."""
        self.channel = channel
        self.video_id = video_id
        self.job_id = job_id
        self.budget = budget
        self.total = 0.0
        self.event_ids = []

    def reserve(self, purpose, model, maximum, units=0):
        """Reserve maximum dollars for a paid call and return its event ID.

        purpose describes the work, model names the service, and units counts
        voice characters if relevant. Unknown outcomes retain this estimate.
        """
        if not math.isfinite(maximum) or maximum < 0:
            raise ValueError("The paid call has no valid price estimate.")
        if self.budget is not None and self.total + maximum > self.budget + 0.000001:
            raise ValueError("This job reached its spending cap. Increase the cap and start a new job.")
        connection = get_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if self.job_id:
                job = connection.execute("SELECT status FROM jobs WHERE id = ?", (self.job_id,)).fetchone()
                if not job or job["status"] != "running":
                    raise ValueError("This job is no longer running.")
                from src.dashboard.settings import job_settings
                if spent_today(connection) + maximum > job_settings().daily_limit_usd + 0.000001:
                    raise ValueError("Today's spending limit has been reached.")
            cursor = connection.execute("""
                INSERT INTO cost_events
                    (video_id, channel, purpose, model, cost_usd, created_at, job_id, state, units)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'uncertain', ?)
            """, (self.video_id, self.channel, purpose, model, maximum, utc_now(), self.job_id, units))
            if self.video_id:
                connection.execute("UPDATE videos SET total_cost_usd = total_cost_usd + ? WHERE id = ?",
                                   (maximum, self.video_id))
            connection.commit()
            self.total += maximum
            self.event_ids.append(cursor.lastrowid)
            return cursor.lastrowid
        finally:
            connection.close()

    def settle(self, event_id, cost, input_tokens=0, output_tokens=0):
        """Replace event_id's estimate with cost and token counts; return nothing."""
        connection = get_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute("SELECT * FROM cost_events WHERE id = ?", (event_id,)).fetchone()
            difference = cost - old["cost_usd"]
            connection.execute("""
                UPDATE cost_events SET cost_usd = ?, input_tokens = ?, output_tokens = ?,
                    state = 'recorded' WHERE id = ?
            """, (cost, input_tokens, output_tokens, event_id))
            if old["video_id"]:
                connection.execute("UPDATE videos SET total_cost_usd = total_cost_usd + ? WHERE id = ?",
                                   (difference, old["video_id"]))
            connection.commit()
            self.total += difference
        finally:
            connection.close()

    def attach_video(self, video_id, add_to_total=True):
        """Link this run's calls to video_id, optionally adding their costs.

        add_to_total is False only when a legacy caller already saved the total.
        Returns nothing; repeated attachment does not add the same cost twice.
        """
        connection = get_connection()
        try:
            for event_id in self.event_ids:
                row = connection.execute("SELECT * FROM cost_events WHERE id = ?", (event_id,)).fetchone()
                if row["video_id"] is None:
                    connection.execute("UPDATE cost_events SET video_id = ? WHERE id = ?", (video_id, event_id))
                    if add_to_total:
                        connection.execute("UPDATE videos SET total_cost_usd = total_cost_usd + ? WHERE id = ?",
                                           (row["cost_usd"], video_id))
            connection.commit()
            self.video_id = video_id
        finally:
            connection.close()

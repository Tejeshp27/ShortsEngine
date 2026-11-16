# =============================================================
# src/storage/finance.py — money in, money out
#
# Two jobs, shared by the command line and the dashboard:
#
#   1. The ledger: one list of every dollar recorded, combining
#      - cost_events:   one row per paid AI or voice call
#      - videos:        older videos whose cost was saved only as a
#                       total (before per-call tracking existed)
#      - money_entries: payouts and expenses you typed in by hand
#
#   2. CostTracker: records each paid call. It saves an estimate
#      BEFORE contacting the paid service and replaces it with the
#      real cost afterwards. If the program crashes in between, the
#      estimate stays visible as "uncertain" instead of vanishing.
# =============================================================

import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from src.storage.database import get_connection

# One SQL query that turns the three money sources into rows with the
# same columns: channel, day, expense, income, note, state, kind, id.
# "UNION ALL" stacks the results of the three SELECTs on top of each other.
#
# Older videos stored their total cost on the video row. To avoid
# counting a call twice, the middle SELECT only counts the part of a
# video's total that is NOT already in cost_events.
#
# Old rows were saved in local time (19 characters, no time zone);
# date(..., 'utc') converts those to a UTC day. The startup migration
# in control_schema.py converts them too; this is a second safety net.
LEDGER_SQL = """
    SELECT channel,
           CASE WHEN length(created_at) = 19 THEN date(created_at, 'utc')
                ELSE date(created_at) END AS day,
           cost_usd AS expense,
           0.0 AS income,
           purpose AS note,
           state,
           'api' AS kind,
           id
    FROM cost_events

    UNION ALL

    SELECT v.channel,
           CASE WHEN length(v.created_at) = 19 THEN date(v.created_at, 'utc')
                ELSE date(v.created_at) END,
           MAX(0, v.total_cost_usd - COALESCE(
               (SELECT SUM(c.cost_usd) FROM cost_events c WHERE c.video_id = v.id), 0)),
           0.0,
           'Earlier video costs',
           'legacy',
           'legacy',
           v.id
    FROM videos v

    UNION ALL

    SELECT channel,
           entry_date,
           CASE WHEN kind = 'expense' THEN amount_cents / 100.0 ELSE 0 END,
           CASE WHEN kind = 'income' THEN amount_cents / 100.0 ELSE 0 END,
           note,
           'recorded',
           kind,
           id
    FROM money_entries
"""

# Small tolerance for comparing dollar amounts stored as decimals.
# 0.1 + 0.2 is 0.30000000000000004 in floating point, so an exact ">"
# test could wrongly refuse a job that fits the limit exactly.
ROUNDING_TOLERANCE = 0.000001

MAX_MONEY_ENTRY_DOLLARS = 1_000_000


def utc_now():
    """
    Get the current time in UTC as text, for saving in the database.

    Returns:
        str: For example "2026-11-11T06:45:00+00:00". Text in this
             format sorts in time order.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ledger(channel=None):
    """
    List every recorded income and expense, newest first.

    Args:
        channel (str or None): Only show this channel, or None for all.

    Returns:
        list[dict]: One dict per entry with "channel", "day", "expense",
                    "income", "note", "state", "kind" and "id".
                    Rows worth $0 are left out.
    """
    query = f"SELECT * FROM ({LEDGER_SQL}) WHERE (expense > 0 OR income > 0)"
    values = []
    if channel:
        query += " AND channel = ?"
        values.append(channel)
    query += " ORDER BY day DESC, id DESC"

    connection = get_connection()
    try:
        rows = []
        for row in connection.execute(query, values):
            rows.append(dict(row))
        return rows
    finally:
        connection.close()


def money_summary(channel=None):
    """
    Add up the ledger in total and per month.

    Args:
        channel (str or None): Only this channel, or None for all channels.

    Returns:
        dict: "totals" (income, expense, profit), "months" (one dict per
              month, newest first) and "entries" (the ledger rows).
    """
    entries = ledger(channel)
    totals = {"income": 0.0, "expense": 0.0, "profit": 0.0}
    months = {}

    for entry in entries:
        month = entry["day"][:7]   # "2026-11-05" → "2026-11"
        if month not in months:
            months[month] = {"month": month, "income": 0.0, "expense": 0.0, "profit": 0.0}

        totals["income"] += entry["income"]
        totals["expense"] += entry["expense"]
        months[month]["income"] += entry["income"]
        months[month]["expense"] += entry["expense"]

    totals["profit"] = totals["income"] - totals["expense"]
    for month in months.values():
        month["profit"] = month["income"] - month["expense"]

    return {"totals": totals, "months": list(months.values()), "entries": entries}


def dollars_to_cents(amount):
    """
    Turn dollar text typed into a form into a whole number of cents.

    Decimal is used instead of float because float cannot store most
    cent values exactly (0.1 is really 0.1000000000000000055...).

    Args:
        amount (str): For example "12.50".

    Returns:
        int: For example 1250.

    Raises:
        ValueError: If the amount is not a number, is zero or less, is
                    too large, or has more than two decimal places.
    """
    try:
        dollars = Decimal(amount)
    except InvalidOperation as error:
        raise ValueError("Enter a valid date and positive dollar amount with at most two decimals.") from error

    if not dollars.is_finite() or dollars <= 0 or dollars > MAX_MONEY_ENTRY_DOLLARS:
        raise ValueError("Enter a valid date and positive dollar amount with at most two decimals.")
    if dollars != dollars.quantize(Decimal("0.01")):
        raise ValueError("Enter a valid date and positive dollar amount with at most two decimals.")
    return int(dollars * 100)


def check_entry_date(entry_date):
    """
    Make sure a date typed into a form is a real YYYY-MM-DD date.

    Args:
        entry_date (str): For example "2026-11-10".

    Raises:
        ValueError: For anything else, e.g. "2026-13-40" or "11/10/2026".
    """
    try:
        parsed = datetime.strptime(entry_date, "%Y-%m-%d").date()
    except ValueError as error:
        raise ValueError("Enter a valid date and positive dollar amount with at most two decimals.") from error
    # strptime accepts "2026-1-5"; insist on the exact padded form.
    if parsed.isoformat() != entry_date:
        raise ValueError("Enter a valid date and positive dollar amount with at most two decimals.")


def add_money_entry(channel, platform, kind, amount, entry_date, note, submission_id):
    """
    Save a payout or expense typed in on the Money page.

    Args:
        channel (str): The channel it belongs to, e.g. "news".
        platform (str): "tiktok", "instagram" or "other".
        kind (str): "income" (money made) or "expense" (money spent).
        amount (str): Dollars as text, e.g. "12.50".
        entry_date (str): The date, as YYYY-MM-DD.
        note (str): Optional description (up to 1,000 characters).
        submission_id (str): A random value from the form. If the same
                             form is sent twice (a double-click), the
                             second copy is ignored.

    Returns:
        int: The ID of the saved entry.

    Raises:
        ValueError: If any value is invalid.
    """
    if kind not in ("income", "expense"):
        raise ValueError("Choose a valid entry type and platform.")
    if platform not in ("tiktok", "instagram", "other"):
        raise ValueError("Choose a valid entry type and platform.")
    cents = dollars_to_cents(amount)
    check_entry_date(entry_date)
    if len(note) > 1000 or not submission_id or len(submission_id) > 100:
        raise ValueError("Reload the form and keep the note under 1,000 characters.")

    connection = get_connection()
    try:
        # INSERT OR IGNORE: if this submission_id was already saved,
        # the unique rule on that column makes SQLite skip the insert.
        connection.execute("""
            INSERT OR IGNORE INTO money_entries
                (channel, platform, kind, amount_cents, entry_date, note, submission_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (channel, platform, kind, cents, entry_date, note, submission_id, utc_now()))
        row = connection.execute(
            "SELECT id FROM money_entries WHERE submission_id = ?", (submission_id,)
        ).fetchone()
        connection.commit()
        return row["id"]
    finally:
        connection.close()


def spent_today(connection):
    """
    Add up today's recorded spending (UTC day).

    Args:
        connection: An open SQLite connection. The caller passes its own
                    so this check can be part of the caller's transaction.

    Returns:
        float: Dollars spent today, including "uncertain" estimates.
    """
    row = connection.execute(
        f"SELECT COALESCE(SUM(expense), 0) AS total FROM ({LEDGER_SQL}) WHERE day = ?",
        (utc_now()[:10],),
    ).fetchone()
    return row["total"]


class CostTracker:
    """
    Records the cost of each paid call in one run and enforces a budget.

    How a paid call is recorded:
        1. reserve(): save the most the call could cost, marked "uncertain".
        2. The call is made.
        3. settle(): replace the estimate with the real cost, marked "recorded".

    Example:
        tracker = CostTracker("news", budget=0.50)
        event_id = tracker.reserve("writing", "claude-sonnet-4-6", 0.12)
        ... make the call ...
        tracker.settle(event_id, 0.04, input_tokens=3000, output_tokens=900)
    """

    def __init__(self, channel, video_id=None, job_id=None, budget=None):
        """
        Start tracking costs for one run.

        Args:
            channel (str): The channel doing the work.
            video_id (int or None): The video the costs belong to, if known yet.
            job_id (str or None): The browser job doing the work, if any.
                                  Browser jobs also obey the daily limit.
            budget (float or None): The most this run may spend, or None
                                    for no per-run limit (command-line runs).
        """
        self.channel = channel
        self.video_id = video_id
        self.job_id = job_id
        self.budget = budget
        self.total = 0.0
        self.event_ids = []

    def reserve(self, purpose, model, maximum, units=0):
        """
        Save the most a paid call could cost, before making the call.

        Args:
            purpose (str): What the call is for, e.g. "writing".
            model (str): The AI model or voice model used.
            maximum (float): The highest possible cost in dollars.
            units (int): Voice characters, for voice calls (else 0).

        Returns:
            int: The ID of the saved cost row, for settle().

        Raises:
            ValueError: If the estimate is invalid, this run's budget or
                        today's limit would be exceeded, or the browser
                        job has been stopped.
        """
        if not math.isfinite(maximum) or maximum < 0:
            raise ValueError("The paid call has no valid price estimate.")
        if self.budget is not None and self.total + maximum > self.budget + ROUNDING_TOLERANCE:
            raise ValueError("This job reached its spending cap. Increase the cap and start a new job.")

        connection = get_connection()
        try:
            # BEGIN IMMEDIATE locks the database for writing right away, so
            # two jobs cannot both read "under the limit" and both spend.
            connection.execute("BEGIN IMMEDIATE")
            if self.job_id:
                self.check_job_may_spend(connection, maximum)

            cursor = connection.execute("""
                INSERT INTO cost_events
                    (video_id, channel, purpose, model, cost_usd, created_at, job_id, state, units)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'uncertain', ?)
            """, (self.video_id, self.channel, purpose, model, maximum, utc_now(), self.job_id, units))
            if self.video_id:
                connection.execute(
                    "UPDATE videos SET total_cost_usd = total_cost_usd + ? WHERE id = ?",
                    (maximum, self.video_id),
                )
            connection.commit()
        finally:
            connection.close()

        self.total += maximum
        self.event_ids.append(cursor.lastrowid)
        return cursor.lastrowid

    def check_job_may_spend(self, connection, maximum):
        """
        Refuse a browser job's paid call if the job was stopped or today's
        limit would be passed.

        Args:
            connection: The open connection inside reserve()'s transaction.
            maximum (float): The call's highest possible cost.

        Raises:
            ValueError: If the call must not be made.
        """
        # Imported here because settings.py lives in the dashboard package,
        # which itself imports this file; importing at the top would loop.
        from src.dashboard.settings import job_settings

        job = connection.execute("SELECT status FROM jobs WHERE id = ?", (self.job_id,)).fetchone()
        if not job or job["status"] != "running":
            raise ValueError("This job is no longer running.")
        daily_limit = job_settings().daily_limit_usd
        if spent_today(connection) + maximum > daily_limit + ROUNDING_TOLERANCE:
            raise ValueError("Today's spending limit has been reached.")

    def settle(self, event_id, cost, input_tokens=0, output_tokens=0):
        """
        Replace a reserved estimate with the call's real cost.

        Args:
            event_id (int): The ID returned by reserve().
            cost (float): The real cost in dollars.
            input_tokens (int): Tokens sent to the model.
            output_tokens (int): Tokens the model wrote.

        Returns:
            Nothing.
        """
        connection = get_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute("SELECT * FROM cost_events WHERE id = ?", (event_id,)).fetchone()
            difference = cost - old["cost_usd"]
            connection.execute("""
                UPDATE cost_events
                SET cost_usd = ?, input_tokens = ?, output_tokens = ?, state = 'recorded'
                WHERE id = ?
            """, (cost, input_tokens, output_tokens, event_id))
            if old["video_id"]:
                connection.execute(
                    "UPDATE videos SET total_cost_usd = total_cost_usd + ? WHERE id = ?",
                    (difference, old["video_id"]),
                )
            connection.commit()
        finally:
            connection.close()
        self.total += difference

    def attach_video(self, video_id, add_to_total=True):
        """
        Link this run's earlier calls to a video once the video exists.

        For example, the script is written (a paid call) before the video
        row is saved, so that call is linked afterwards.

        Args:
            video_id (int): The video the costs belong to.
            add_to_total (bool): Also add the costs to the video's total.
                                 False only when the caller already saved
                                 the total itself (the older command-line path).

        Returns:
            Nothing. Calling it twice does not add the same cost twice,
            because only calls with no video yet are changed.
        """
        connection = get_connection()
        try:
            for event_id in self.event_ids:
                row = connection.execute("SELECT * FROM cost_events WHERE id = ?", (event_id,)).fetchone()
                if row["video_id"] is not None:
                    continue
                connection.execute("UPDATE cost_events SET video_id = ? WHERE id = ?", (video_id, event_id))
                if add_to_total:
                    connection.execute(
                        "UPDATE videos SET total_cost_usd = total_cost_usd + ? WHERE id = ?",
                        (row["cost_usd"], video_id),
                    )
            connection.commit()
        finally:
            connection.close()
        self.video_id = video_id

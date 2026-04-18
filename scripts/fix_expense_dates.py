#!/usr/bin/env python3
"""
Fix 3 (ITERATION-P2): Repair wrong dates in the dashboard expenses table.

Problem:
  - 37 telegram-sourced expenses were synced with date=2026-04-13 (the sync date)
    instead of the actual receipt date. The receipt-bot DB at
    /home/asimo/.agila-telegram/receipts.db has correct date_str values.
  - Some onedrive-sourced expenses may have dashboard.date disagreeing with the
    YYYYMMDD prefix of the source filename.

Strategy:
  1. Telegram expenses:
     - Look up the correct date_str in the bot DB by receipt_id (uuid).
     - If another dashboard row exists with the same receipt_id AND the correct
       date, the current wrong-date row is a duplicate and is DELETED.
     - Otherwise, the wrong-date row is UPDATED to the bot's date_str.
  2. OneDrive expenses:
     - If the filename (notes field "File: YYYYMMDD_...") contains a date prefix
       and it differs from dashboard.date, UPDATE dashboard.date to match.

The script is idempotent — rows already correct are skipped.
"""
import re
import sqlite3
from pathlib import Path

DASHBOARD_DB = Path("/home/asimo/agila-financial-dashboard/agila.db")
BOT_DB = Path("/home/asimo/.agila-telegram/receipts.db")


def load_bot_receipts(bot_conn):
    bot_conn.row_factory = sqlite3.Row
    rows = bot_conn.execute(
        "SELECT uuid, date_str, vendor, amount, currency, filename FROM receipts"
    ).fetchall()
    return {r["uuid"]: dict(r) for r in rows if r["uuid"]}


def fix_telegram_dates(dash_conn, bot_receipts):
    dash_conn.row_factory = sqlite3.Row
    rows = dash_conn.execute(
        "SELECT id, date, vendor, amount, receipt_id FROM expenses "
        "WHERE source='telegram' ORDER BY id"
    ).fetchall()

    updates, deletes, skips, missing = 0, 0, 0, 0

    by_receipt = {}
    for r in rows:
        by_receipt.setdefault(r["receipt_id"], []).append(dict(r))

    for receipt_id, entries in by_receipt.items():
        bot = bot_receipts.get(receipt_id)
        if not bot:
            missing += 1
            print(f"  MISSING in bot DB: receipt_id={receipt_id} "
                  f"(vendors: {[e['vendor'] for e in entries]})")
            continue

        correct_date = bot["date_str"]
        if not correct_date:
            missing += 1
            print(f"  NO date_str in bot for receipt_id={receipt_id}")
            continue

        # Partition entries into correct-date and wrong-date
        correct_entries = [e for e in entries if e["date"] == correct_date]
        wrong_entries = [e for e in entries if e["date"] != correct_date]

        if not wrong_entries:
            skips += len(entries)
            continue

        if correct_entries:
            # Wrong-date rows are duplicates of correct-date row — delete them.
            for w in wrong_entries:
                dash_conn.execute("DELETE FROM expenses WHERE id=?", (w["id"],))
                deletes += 1
                print(f"  DELETE duplicate id={w['id']} "
                      f"(wrong date {w['date']}, kept correct-date row); "
                      f"vendor={w['vendor']}, receipt_id={receipt_id}")
        else:
            # No correct-date row exists — update the wrong-date row(s).
            # If more than one wrong-date row exists for the same receipt_id,
            # update the first and delete the rest to avoid duplicates.
            primary, *extras = wrong_entries
            dash_conn.execute(
                "UPDATE expenses SET date=? WHERE id=?",
                (correct_date, primary["id"]),
            )
            updates += 1
            print(f"  UPDATE id={primary['id']} date {primary['date']} -> "
                  f"{correct_date}; vendor={primary['vendor']}, "
                  f"receipt_id={receipt_id}")
            for ex in extras:
                dash_conn.execute("DELETE FROM expenses WHERE id=?", (ex["id"],))
                deletes += 1
                print(f"  DELETE extra id={ex['id']} for same receipt_id")

    return updates, deletes, skips, missing


FILENAME_DATE_RE = re.compile(r"(\d{8})_")


def fix_onedrive_dates(dash_conn):
    dash_conn.row_factory = sqlite3.Row
    rows = dash_conn.execute(
        "SELECT id, date, vendor, notes FROM expenses WHERE source='onedrive'"
    ).fetchall()

    updates, skips = 0, 0
    for r in rows:
        notes = r["notes"] or ""
        m = re.search(r"File:\s*(\d{8})_", notes)
        if not m:
            skips += 1
            continue
        ymd = m.group(1)
        try:
            iso = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        except Exception:
            skips += 1
            continue
        if r["date"] == iso:
            skips += 1
            continue
        dash_conn.execute("UPDATE expenses SET date=? WHERE id=?", (iso, r["id"]))
        updates += 1
        print(f"  UPDATE onedrive id={r['id']} date {r['date']} -> {iso}; "
              f"vendor={r['vendor']}")
    return updates, skips


def main():
    dash_conn = sqlite3.connect(str(DASHBOARD_DB))
    bot_conn = sqlite3.connect(str(BOT_DB))

    print("Loading bot DB receipts...")
    bot_receipts = load_bot_receipts(bot_conn)
    print(f"  {len(bot_receipts)} receipts in bot DB")

    print("\n=== Fixing telegram-sourced dashboard expenses ===")
    t_upd, t_del, t_skip, t_miss = fix_telegram_dates(dash_conn, bot_receipts)

    print("\n=== Fixing onedrive-sourced dashboard expenses ===")
    o_upd, o_skip = fix_onedrive_dates(dash_conn)

    dash_conn.execute(
        "INSERT INTO sync_log (source, action, records_affected, status) "
        "VALUES ('fix_script','fix_expense_dates',?,?)",
        (t_upd + t_del + o_upd, "ok"),
    )

    dash_conn.commit()
    dash_conn.close()
    bot_conn.close()

    print("\n=== Summary ===")
    print(f"Telegram: updated={t_upd} deleted_dups={t_del} "
          f"skipped={t_skip} missing_in_bot={t_miss}")
    print(f"OneDrive: updated={o_upd} skipped={o_skip}")


if __name__ == "__main__":
    main()

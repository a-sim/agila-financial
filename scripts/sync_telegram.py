#!/usr/bin/env python3
"""
Sync receipts from Telegram bot DB (~/.agila-telegram/receipts.db)
into the agila dashboard expenses table.
"""
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "agila.db"
RECEIPTS_DB = Path.home() / ".agila-telegram" / "receipts.db"

VAT_RATES = {
    'restaurant': 0.17,
    'hotel': 0.03,
    'travel': 0.17,
    'flight': 0.17,
    'taxi': 0.17,
    'software': 0.17,
    'subscription': 0.17,
    'office': 0.17,
    'supplies': 0.17,
    'professional': 0.17,
    'professional_services': 0.17,
    'other': 0.17,
}

NON_RECOVERABLE = {'restaurant'}


def sync():
    if not RECEIPTS_DB.exists():
        print(f"Telegram receipts DB not found at {RECEIPTS_DB}. Skipping.")
        return 0

    # Connect to both databases
    agila_conn = sqlite3.connect(str(DB_PATH))
    agila_conn.row_factory = sqlite3.Row
    agila_cur = agila_conn.cursor()

    tg_conn = sqlite3.connect(str(RECEIPTS_DB))
    tg_conn.row_factory = sqlite3.Row
    tg_cur = tg_conn.cursor()

    # Get all receipts from Telegram DB
    try:
        tg_cur.execute("SELECT * FROM receipts ORDER BY created_at")
        receipts = [dict(row) for row in tg_cur.fetchall()]
    except Exception as e:
        print(f"Error reading Telegram receipts: {e}")
        tg_conn.close()
        agila_conn.close()
        return 0

    count = 0
    for r in receipts:
        uuid = r.get("uuid") or r.get("id")
        if not uuid:
            continue

        # Check if already imported
        agila_cur.execute("SELECT id FROM expenses WHERE receipt_id = ?", (str(uuid),))
        if agila_cur.fetchone():
            continue

        amount = r.get("amount") or 0.0
        category = r.get("category") or "other"
        vendor = r.get("vendor") or r.get("description") or ""
        date = r.get("date") or r.get("created_at") or ""
        # Normalize date to YYYY-MM-DD
        if date and len(date) > 10:
            date = date[:10]
        notes = r.get("notes") or r.get("caption") or ""

        vat_rate = VAT_RATES.get(category, 0.17)
        amount_vat = round(amount * vat_rate, 2)
        if category in NON_RECOVERABLE:
            vat_recoverable = 0.0
        else:
            vat_recoverable = amount_vat

        agila_cur.execute("""
            INSERT INTO expenses (date, amount, category, vendor, notes, source,
                amount_vat, vat_rate, vat_recoverable, receipt_id, description)
            VALUES (?, ?, ?, ?, ?, 'telegram', ?, ?, ?, ?, ?)
        """, (
            date, amount, category, vendor, notes,
            amount_vat, vat_rate, vat_recoverable, str(uuid), notes,
        ))
        count += 1
        print(f"  Imported: {date} | EUR {amount:.2f} | {category} | {vendor}")

    # Log sync
    agila_cur.execute(
        "INSERT INTO sync_log (source, action, records_affected, status) VALUES (?,?,?,?)",
        ("telegram", "sync_receipts", count, "ok"),
    )

    agila_conn.commit()
    agila_conn.close()
    tg_conn.close()

    print(f"Telegram sync complete: {count} new receipts imported.")
    return count


if __name__ == "__main__":
    sync()

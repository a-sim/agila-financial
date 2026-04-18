#!/usr/bin/env python3
"""
Fix 4 (ITERATION-P2): Foreign-currency receipt corrections.

Two classes of problem:
  1. THE ITALIAN CORNER receipt (uuid 1a11ef07) was saved as 804 EUR but the
     receipt was actually 804 DKK. Convert to EUR using the ECB reference rate
     for the receipt date (2026-03-11) and update bot + dashboard rows.
  2. For other already-converted foreign-currency receipts (Metrostation,
     Mefjord, Mors Mat AS), the dashboard row's amount is correct EUR but the
     notes/description may not expose the original amount. Refresh the notes
     so "Original: X.XX CUR -> Y.YY EUR" is visible in the dashboard.

The script uses Frankfurter (frankfurter.app) for historical ECB rates — same
source the receipt bot already uses.
"""
import json
import sqlite3
import urllib.request
from pathlib import Path

DASHBOARD_DB = Path("/home/asimo/agila-financial-dashboard/agila.db")
BOT_DB = Path("/home/asimo/.agila-telegram/receipts.db")

ITALIAN_CORNER_UUID = "1a11ef07"
ITALIAN_CORNER_AMOUNT_DKK = 804.0
ITALIAN_CORNER_DATE = "2026-03-11"


def fetch_rate(currency: str, tx_date: str) -> float:
    """Fetch the ECB reference rate for `currency` on `tx_date`.
    Returns rate such that 1 EUR = rate * <currency>."""
    req = urllib.request.Request(
        f"https://api.frankfurter.app/{tx_date}",
        headers={"User-Agent": "agila-fix/1.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.load(r)
    rate = data.get("rates", {}).get(currency.upper())
    if rate is None:
        raise RuntimeError(f"No ECB rate for {currency} on {tx_date}")
    return float(rate)


def fix_italian_corner(dash_conn, bot_conn):
    rate = fetch_rate("DKK", ITALIAN_CORNER_DATE)
    amount_eur = round(ITALIAN_CORNER_AMOUNT_DKK / rate, 2)
    print(f"Italian Corner: {ITALIAN_CORNER_AMOUNT_DKK} DKK @ {rate} DKK/EUR "
          f"(ECB {ITALIAN_CORNER_DATE}) -> {amount_eur} EUR")

    # Update bot DB: set currency=DKK, amount_original=804, amount=amount_eur
    bot_conn.execute(
        "UPDATE receipts SET currency=?, amount_original=?, amount=? "
        "WHERE uuid=?",
        ("DKK", ITALIAN_CORNER_AMOUNT_DKK, amount_eur, ITALIAN_CORNER_UUID),
    )
    bot_conn.commit()

    # Dashboard telegram rows for this uuid
    dash_conn.row_factory = sqlite3.Row
    rows = dash_conn.execute(
        "SELECT id, date, vendor, amount, notes, description "
        "FROM expenses WHERE source='telegram' AND receipt_id=?",
        (ITALIAN_CORNER_UUID,),
    ).fetchall()

    tel_updates = 0
    for r in rows:
        new_notes = (
            f"Original: {ITALIAN_CORNER_AMOUNT_DKK:.2f} DKK -> "
            f"{amount_eur:.2f} EUR (ECB rate {rate} on {ITALIAN_CORNER_DATE})"
        )
        dash_conn.execute(
            "UPDATE expenses SET amount=?, notes=?, description=? "
            "WHERE id=?",
            (amount_eur, new_notes, new_notes, r["id"]),
        )
        tel_updates += 1
        print(f"  telegram id={r['id']}: amount {r['amount']} -> {amount_eur}")

    # Dashboard onedrive rows for the same filename (amount 804, vendor THEITALIANCORNER)
    od_rows = dash_conn.execute(
        "SELECT id, date, vendor, amount, notes, onedrive_id "
        "FROM expenses WHERE source='onedrive' "
        "AND (vendor LIKE '%ITALIANCORNER%' OR vendor LIKE '%Italian Corner%') "
        "AND date=?",
        (ITALIAN_CORNER_DATE,),
    ).fetchall()

    od_updates = 0
    for r in od_rows:
        notes = r["notes"] or ""
        # Fix filename reference in notes (EUR -> DKK) while preserving path
        new_notes_fn = notes.replace("804.00EUR", "804.00DKK")
        prefix = (
            f"Original: {ITALIAN_CORNER_AMOUNT_DKK:.2f} DKK -> "
            f"{amount_eur:.2f} EUR (ECB rate {rate} on {ITALIAN_CORNER_DATE}). "
        )
        new_notes = prefix + new_notes_fn
        dash_conn.execute(
            "UPDATE expenses SET amount=?, notes=?, description=? "
            "WHERE id=?",
            (amount_eur, new_notes, new_notes, r["id"]),
        )
        od_updates += 1
        print(f"  onedrive id={r['id']}: amount {r['amount']} -> {amount_eur}")

    return tel_updates + od_updates


def refresh_foreign_notes(dash_conn, bot_conn):
    """For every bot receipt with currency != EUR and amount_original set,
    ensure the matching dashboard telegram row exposes the original amount
    in its notes/description."""
    bot_conn.row_factory = sqlite3.Row
    bot_rows = bot_conn.execute(
        "SELECT uuid, date_str, vendor, amount, amount_original, currency "
        "FROM receipts WHERE currency IS NOT NULL AND UPPER(currency) != 'EUR'"
    ).fetchall()

    updates = 0
    for b in bot_rows:
        uuid = b["uuid"]
        if uuid == ITALIAN_CORNER_UUID:
            continue  # already handled
        if not b["amount_original"]:
            continue
        note = (
            f"Original: {b['amount_original']:.2f} {b['currency'].upper()} -> "
            f"{b['amount']:.2f} EUR"
        )
        # Dashboard rows tied to this uuid
        rows = dash_conn.execute(
            "SELECT id, amount, notes FROM expenses "
            "WHERE source='telegram' AND receipt_id=?",
            (uuid,),
        ).fetchall()
        for r in rows:
            current = r["notes"] or ""
            if note in current:
                continue
            # Replace notes wholesale for clarity; if there were prior manual
            # notes, prepend them to preserve the information.
            existing_extra = current.strip()
            if existing_extra and not existing_extra.startswith("Original:"):
                new_notes = f"{note}. {existing_extra}"
            else:
                new_notes = note
            dash_conn.execute(
                "UPDATE expenses SET notes=?, description=? WHERE id=?",
                (new_notes, new_notes, r["id"]),
            )
            updates += 1
            print(f"  telegram id={r['id']} ({b['vendor']}): notes refreshed -> {note}")
    return updates


def main():
    dash_conn = sqlite3.connect(str(DASHBOARD_DB))
    bot_conn = sqlite3.connect(str(BOT_DB))

    print("=== Fixing THE ITALIAN CORNER (804 EUR -> 804 DKK) ===")
    ic_updates = fix_italian_corner(dash_conn, bot_conn)

    print("\n=== Refreshing foreign-currency notes on other rows ===")
    other_updates = refresh_foreign_notes(dash_conn, bot_conn)

    dash_conn.execute(
        "INSERT INTO sync_log (source, action, records_affected, status) "
        "VALUES ('fix_script','fix_currency_expenses',?,?)",
        (ic_updates + other_updates, "ok"),
    )
    dash_conn.commit()
    dash_conn.close()
    bot_conn.close()

    print("\n=== Summary ===")
    print(f"Italian Corner rows updated: {ic_updates}")
    print(f"Other foreign-currency notes refreshed: {other_updates}")


if __name__ == "__main__":
    main()

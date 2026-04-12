#!/usr/bin/env python3
"""
Sync invoices and bank entries from Odoo into agila.db.
Uses the same JSON-RPC approach as backend/services/odoo_service.py.
"""
import json
import sqlite3
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "agila.db"

ODOO_URL = "https://agila-consulting-sarl.odoo.com/jsonrpc"
DB = "agila-consulting-sarl"
API_KEY = "REDACTED_ODOO_API_KEY"
UID = 2


def _call(service, method, args):
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method": "call",
        "params": {"service": service, "method": method, "args": args},
        "id": 1,
    }).encode()
    req = urllib.request.Request(
        ODOO_URL, data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
    if "error" in result:
        raise Exception(result["error"].get("message", str(result["error"])))
    return result.get("result", [])


def log_sync(cur, source, action, records_affected, status, error=None):
    cur.execute(
        "INSERT INTO sync_log (source, action, records_affected, status, error) VALUES (?,?,?,?,?)",
        (source, action, records_affected, status, error),
    )


def sync_invoices(cur):
    """Pull all posted out_invoice records from Odoo, upsert into invoices table."""
    print("Syncing invoices from Odoo ...")
    fields = [
        "name", "invoice_date", "invoice_date_due", "amount_total",
        "amount_residual", "partner_id", "state", "move_type", "id",
    ]
    records = _call("object", "execute_kw", [
        DB, UID, API_KEY,
        "account.move", "search_read",
        [[["move_type", "=", "out_invoice"], ["state", "=", "posted"]]],
        {"fields": fields, "order": "invoice_date desc"},
    ])

    count = 0
    for r in records:
        odoo_id = str(r["id"])
        partner = r.get("partner_id") or [0, "Unknown"]
        partner_name = partner[1] if isinstance(partner, list) else str(partner)
        partner_id = partner[0] if isinstance(partner, list) else None

        invoice_date = r.get("invoice_date") or ""
        due_date = r.get("invoice_date_due") or ""
        amount_total = r.get("amount_total") or 0.0
        amount_residual = r.get("amount_residual") or 0.0

        # EU B2B reverse charge: VAT = 0
        status = "paid" if amount_residual == 0 else "posted"

        # Upsert: if odoo_id exists, update; otherwise insert
        cur.execute("SELECT id FROM invoices WHERE odoo_id = ?", (odoo_id,))
        existing = cur.fetchone()
        if existing:
            cur.execute("""
                UPDATE invoices SET name=?, date=?, amount_gross=?, amount_net=?,
                    client=?, status=?, due_date=?, client_supplier_name=?,
                    amount_residual=?
                WHERE odoo_id = ?
            """, (
                r["name"], invoice_date, amount_total, amount_total,
                partner_name, status, due_date, partner_name,
                amount_residual, odoo_id,
            ))
        else:
            cur.execute("""
                INSERT INTO invoices
                (odoo_id, name, date, amount_gross, amount_vat, amount_net,
                 client, status, category, type, due_date, vat_rate,
                 vat_recoverable, client_supplier_id, client_supplier_name,
                 amount_residual)
                VALUES (?, ?, ?, ?, 0, ?, ?, ?, 'consulting', 'outbound', ?, 0, 0, ?, ?, ?)
            """, (
                odoo_id, r["name"], invoice_date, amount_total,
                amount_total, partner_name, status,
                due_date, partner_id, partner_name, amount_residual,
            ))
        count += 1
        print(f"  {r['name']} | {invoice_date} | EUR {amount_total:.2f} | residual: EUR {amount_residual:.2f} | {status}")

    log_sync(cur, "odoo", "sync_invoices", count, "ok")
    print(f"  Synced {count} invoices.")
    return count


def sync_bank_entries(cur):
    """Pull bank statement lines from Odoo."""
    print("Syncing bank entries from Odoo ...")

    # Try account.bank.statement.line first for richer data
    count = 0
    try:
        fields = [
            "id", "date", "amount", "payment_ref", "partner_id",
            "journal_id", "move_id",
        ]
        records = _call("object", "execute_kw", [
            DB, UID, API_KEY,
            "account.bank.statement.line", "search_read",
            [[]],
            {"fields": fields, "order": "date desc", "limit": 500},
        ])

        for r in records:
            odoo_id = r["id"]
            date = r.get("date") or ""
            amount = r.get("amount") or 0.0
            payment_ref = r.get("payment_ref") or ""
            partner = r.get("partner_id") or [0, ""]
            partner_name = partner[1] if isinstance(partner, list) else str(partner)
            journal = r.get("journal_id") or [0, ""]
            journal_name = journal[1] if isinstance(journal, list) else str(journal)

            cur.execute("SELECT id FROM bank_transactions WHERE odoo_id = ?", (odoo_id,))
            existing = cur.fetchone()
            if existing:
                cur.execute("""
                    UPDATE bank_transactions SET date=?, amount=?, partner=?,
                        description=?, journal_name=?
                    WHERE odoo_id = ?
                """, (date, amount, partner_name, payment_ref, journal_name, odoo_id))
            else:
                cur.execute("""
                    INSERT INTO bank_transactions
                    (odoo_id, date, amount, partner, description, category,
                     source, journal_name, reconciliation_status, currency)
                    VALUES (?, ?, ?, ?, ?, 'bank_statement', 'odoo_sync', ?, 'unmatched', 'EUR')
                """, (
                    odoo_id, date, amount, partner_name, payment_ref, journal_name,
                ))
            count += 1

        print(f"  Synced {count} bank statement lines.")
    except Exception as e:
        print(f"  bank.statement.line failed: {e}")
        print("  Falling back to account.move.line ...")

        # Fallback: account.move.line filtered by bank journals
        try:
            # First find bank journal IDs
            journals = _call("object", "execute_kw", [
                DB, UID, API_KEY,
                "account.journal", "search_read",
                [[["type", "=", "bank"]]],
                {"fields": ["id", "name"]},
            ])
            journal_ids = [j["id"] for j in journals]
            journal_map = {j["id"]: j["name"] for j in journals}

            if journal_ids:
                fields = ["id", "date", "debit", "credit", "name", "partner_id", "journal_id"]
                records = _call("object", "execute_kw", [
                    DB, UID, API_KEY,
                    "account.move.line", "search_read",
                    [[["journal_id", "in", journal_ids]]],
                    {"fields": fields, "order": "date desc", "limit": 500},
                ])

                for r in records:
                    odoo_id = r["id"]
                    date = r.get("date") or ""
                    amount = (r.get("debit") or 0.0) - (r.get("credit") or 0.0)
                    description = r.get("name") or ""
                    partner = r.get("partner_id") or [0, ""]
                    partner_name = partner[1] if isinstance(partner, list) else str(partner)
                    j_id = r.get("journal_id")
                    j_name = journal_map.get(j_id[0] if isinstance(j_id, list) else j_id, "")

                    cur.execute("""
                        INSERT OR REPLACE INTO bank_transactions
                        (odoo_id, date, amount, partner, description, category,
                         source, journal_name, reconciliation_status, currency)
                        VALUES (?, ?, ?, ?, ?, 'move_line', 'odoo_sync', ?, 'unmatched', 'EUR')
                    """, (
                        odoo_id, date, amount, partner_name, description, j_name,
                    ))
                    count += 1

                print(f"  Synced {count} move lines (fallback).")
        except Exception as e2:
            print(f"  Fallback also failed: {e2}")
            log_sync(cur, "odoo", "sync_bank_entries", 0, "error", str(e2))
            return 0

    log_sync(cur, "odoo", "sync_bank_entries", count, "ok")
    return count


def main():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        inv_count = sync_invoices(cur)
        bank_count = sync_bank_entries(cur)
        conn.commit()
        print(f"\nOdoo sync complete: {inv_count} invoices, {bank_count} bank entries.")
    except Exception as e:
        print(f"Sync failed: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()

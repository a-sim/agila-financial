#!/usr/bin/env python3
"""
Migrate agila.db to v2 schema.
Adds columns to invoices, bank_transactions.
Rebuilds expenses table to replace boolean vat_recoverable with REAL.
"""
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "agila.db"


def get_columns(cur, table):
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def migrate():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    print(f"Migrating {DB_PATH} ...")

    # ---- invoices: add columns if missing ----
    inv_cols = get_columns(cur, "invoices")
    inv_adds = {
        "type": "TEXT DEFAULT 'outbound'",
        "due_date": "TEXT",
        "vat_rate": "REAL DEFAULT 0",
        "vat_recoverable": "REAL DEFAULT 0",
        "client_supplier_id": "INTEGER",
        "client_supplier_name": "TEXT",
        "odoo_link": "TEXT",
        "amount_residual": "REAL DEFAULT 0",
    }
    for col, typedef in inv_adds.items():
        if col not in inv_cols:
            print(f"  invoices: adding {col}")
            cur.execute(f"ALTER TABLE invoices ADD COLUMN {col} {typedef}")

    # ---- bank_transactions: add columns if missing ----
    bt_cols = get_columns(cur, "bank_transactions")
    bt_adds = {
        "vendor_inferred": "TEXT",
        "journal_name": "TEXT",
        "matched_to_expense_id": "INTEGER",
        "matched_to_invoice_id": "INTEGER",
        "match_confidence": "REAL DEFAULT 0",
        "reconciliation_status": "TEXT DEFAULT 'unmatched'",
        "currency": "TEXT DEFAULT 'EUR'",
    }
    for col, typedef in bt_adds.items():
        if col not in bt_cols:
            print(f"  bank_transactions: adding {col}")
            cur.execute(f"ALTER TABLE bank_transactions ADD COLUMN {col} {typedef}")

    # ---- expenses: rebuild table to convert vat_recoverable from INTEGER to REAL ----
    # and add new columns
    print("  expenses: rebuilding table with new schema ...")
    cur.execute("PRAGMA table_info(expenses)")
    old_cols = {row[1]: row[2] for row in cur.fetchall()}

    # Check if already migrated (vat_recoverable is REAL and has new columns)
    if "matched_to_bank_id" in old_cols and old_cols.get("vat_recoverable", "").upper() == "REAL":
        print("  expenses: already migrated, skipping rebuild")
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS expenses_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                vendor TEXT,
                notes TEXT,
                onedrive_id TEXT,
                source TEXT DEFAULT 'manual',
                amount_vat REAL DEFAULT 0,
                vat_rate REAL DEFAULT 0,
                vat_recoverable REAL DEFAULT 0,
                matched_to_bank_id INTEGER,
                receipt_id TEXT,
                description TEXT
            )
        """)

        # Copy data, converting boolean vat_recoverable to REAL amount
        cur.execute("SELECT * FROM expenses")
        rows = cur.fetchall()
        col_names = [desc[0] for desc in cur.description]

        for row in rows:
            d = dict(zip(col_names, row))
            old_vr = d.get("vat_recoverable", 0)
            amount = d.get("amount", 0)
            category = d.get("category", "other")

            # Determine VAT rate based on category
            if category == "hotel":
                vat_rate = 0.03
            else:
                vat_rate = 0.17

            # Convert: old boolean 1 -> actual VAT amount, 0 -> 0
            if old_vr and old_vr != 0:
                new_vr = round(amount * vat_rate, 2)
                new_amount_vat = round(amount * vat_rate, 2)
            else:
                new_vr = 0.0
                # Restaurant still has VAT charged, just not recoverable
                if category == "restaurant":
                    new_amount_vat = round(amount * 0.17, 2)
                else:
                    new_amount_vat = 0.0

            cur.execute("""
                INSERT INTO expenses_v2 (id, date, amount, category, vendor, notes,
                    onedrive_id, source, amount_vat, vat_rate, vat_recoverable,
                    description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d.get("id"), d["date"], amount, category,
                d.get("vendor"), d.get("notes"),
                d.get("onedrive_id"), d.get("source", "manual"),
                new_amount_vat, vat_rate, new_vr,
                d.get("notes"),  # copy notes into description too
            ))

        cur.execute("DROP TABLE expenses")
        cur.execute("ALTER TABLE expenses_v2 RENAME TO expenses")
        print(f"  expenses: migrated {len(rows)} rows")

    # ---- Create sync_log if not exists ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY,
            source TEXT,
            action TEXT,
            records_affected INTEGER,
            status TEXT,
            error TEXT,
            synced_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "agila.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cur = conn.cursor()

    # v2 schema — matches SPEC.md
    cur.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            odoo_id TEXT UNIQUE,
            name TEXT NOT NULL,
            type TEXT DEFAULT 'outbound',
            date TEXT NOT NULL,
            due_date TEXT,
            amount_gross REAL NOT NULL,
            amount_vat REAL DEFAULT 0,
            amount_net REAL NOT NULL,
            amount_residual REAL DEFAULT 0,
            vat_rate REAL DEFAULT 0,
            vat_recoverable REAL DEFAULT 0,
            client_supplier_id INTEGER,
            client_supplier_name TEXT,
            status TEXT DEFAULT 'draft',
            category TEXT DEFAULT 'consulting',
            odoo_link TEXT,
            notes TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            amount REAL NOT NULL,
            amount_vat REAL DEFAULT 0,
            category TEXT NOT NULL,
            vendor TEXT,
            notes TEXT,
            description TEXT,
            vat_rate REAL DEFAULT 0,
            vat_recoverable REAL DEFAULT 0,
            onedrive_id TEXT,
            onedrive_path TEXT,
            receipt_id TEXT,
            source TEXT DEFAULT 'manual',
            matched_to_bank_id INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bank_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            odoo_id INTEGER UNIQUE,
            date TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT DEFAULT 'EUR',
            partner TEXT,
            description TEXT,
            vendor_inferred TEXT,
            category TEXT,
            source TEXT DEFAULT 'odoo_revolut',
            journal_name TEXT,
            matched_to_expense_id INTEGER,
            matched_to_invoice_id INTEGER,
            match_confidence REAL DEFAULT 0,
            reconciliation_status TEXT DEFAULT 'unmatched',
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS vat_returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quarter TEXT CHECK(quarter IN ('Q1','Q2','Q3','Q4')),
            year INTEGER NOT NULL,
            total_output_vat REAL DEFAULT 0,
            total_input_vat REAL DEFAULT 0,
            net_vat_due REAL DEFAULT 0,
            status TEXT CHECK(status IN ('draft','submitted','paid')),
            due_date TEXT,
            filed_date TEXT,
            filed_by TEXT,
            notes TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT CHECK(type IN ('client','supplier','both')),
            vat_number TEXT,
            country TEXT,
            notes TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            action TEXT NOT NULL,
            records_affected INTEGER,
            status TEXT,
            error TEXT,
            synced_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS reconciliation_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT,
            date TEXT,
            bank TEXT,
            description TEXT,
            amount REAL,
            currency TEXT,
            match_status TEXT,
            receipt_file TEXT,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Database initialized (v2 schema).")
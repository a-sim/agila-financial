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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            odoo_id TEXT,
            name TEXT NOT NULL,
            date TEXT NOT NULL,
            amount_gross REAL NOT NULL,
            amount_vat REAL NOT NULL,
            amount_net REAL NOT NULL,
            client TEXT NOT NULL,
            status TEXT DEFAULT 'draft',
            category TEXT DEFAULT 'consulting'
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            vendor TEXT,
            notes TEXT,
            vat_recoverable INTEGER DEFAULT 0,
            onedrive_id TEXT,
            source TEXT DEFAULT 'manual'
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS vat_returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quarter INTEGER NOT NULL,
            year INTEGER NOT NULL,
            output_vat REAL DEFAULT 0,
            input_vat REAL DEFAULT 0,
            net_vat REAL DEFAULT 0,
            status TEXT DEFAULT 'pending',
            due_date TEXT,
            filed_date TEXT
        )
    """)

    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized.")

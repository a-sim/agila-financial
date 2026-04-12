#!/usr/bin/env python3
"""Seed the agila.db with known invoice, expense, and VAT data."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from database import init_db, get_connection

def seed():
    init_db()
    conn = get_connection()
    cur = conn.cursor()

    # Check if already seeded
    cur.execute("SELECT COUNT(*) FROM invoices")
    if cur.fetchone()[0] > 0:
        print("Database already seeded. Skipping.")
        conn.close()
        return

    # --- Invoices ---
    invoices = [
        ("ext", "INV/2025/00001", "2025-12-15", 9975.00, 1522.73, 8452.27, "Mayker NV", "paid", "consulting"),
        ("ext", "INV/2026/00001", "2026-01-20", 9375.00, 1431.82, 7943.18, "Mayker NV", "paid", "consulting"),
        ("ext", "INV/2026/00002", "2026-02-18", 11250.00, 1718.59, 9531.41, "Mayker NV", "paid", "consulting"),
        ("ext", "INV/2026/00003", "2026-03-20", 12968.75, 1980.59, 10988.16, "Mayker NV", "paid", "consulting"),
    ]

    for row in invoices:
        cur.execute("""
            INSERT INTO invoices (odoo_id, name, date, amount_gross, amount_vat, amount_net, client, status, category)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, row)

    # --- Expenses (realistic Q1 2026 estimates) ---
    expenses = [
        # Jan 2026
        ("2026-01-05", 89.50, "restaurant", "Le Rive", "Team dinner", 0),
        ("2026-01-10", 49.00, "software", "Zoom", "Monthly subscription", 1),
        ("2026-01-15", 120.00, "travel", "LuxAir", "Flight BXL-LUX", 1),
        ("2026-01-22", 35.00, "office", "Amazon", "Office supplies", 1),
        ("2026-01-28", 210.00, "professional_services", "ATTC", "January retainer", 0),
        # Feb 2026
        ("2026-02-03", 67.80, "restaurant", "Mudanza", "Client lunch", 0),
        ("2026-02-07", 49.00, "software", "Zoom", "Monthly subscription", 1),
        ("2026-02-14", 180.00, "travel", "Siemens Hotel", "Meeting in BXL", 1),
        ("2026-02-19", 55.00, "office", "Amazon", "Office supplies", 1),
        ("2026-02-25", 210.00, "professional_services", "ATTC", "February retainer", 0),
        # Mar 2026
        ("2026-03-04", 95.00, "restaurant", "Restaurant 4", "Team dinner", 0),
        ("2026-03-10", 49.00, "software", "Zoom", "Monthly subscription", 1),
        ("2026-03-12", 320.00, "travel", "LuxAir", "Flight LUX-BCN", 1),
        ("2026-03-18", 88.00, "software", "Notion", "Annual subscription", 1),
        ("2026-03-24", 210.00, "professional_services", "ATTC", "March retainer", 0),
        ("2026-03-28", 42.00, "office", "Amazon", "Office supplies", 1),
        # April 2026 (partial)
        ("2026-04-03", 78.00, "restaurant", "Le Globe", "Business lunch", 0),
        ("2026-04-08", 49.00, "software", "Zoom", "Monthly subscription", 1),
    ]

    for row in expenses:
        cur.execute("""
            INSERT INTO expenses (date, amount, category, vendor, notes, vat_recoverable)
            VALUES (?, ?, ?, ?, ?, ?)
        """, row)

    # --- VAT Returns ---
    cur.execute("""
        INSERT INTO vat_returns (quarter, year, output_vat, input_vat, net_vat, status, due_date)
        VALUES (1, 2026, 5130.93, 850.00, 4280.93, 'pending', '2026-06-15')
    """)

    conn.commit()
    conn.close()
    print("Seed data inserted successfully.")

if __name__ == "__main__":
    seed()

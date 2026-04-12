from fastapi import APIRouter
from database import get_connection

router = APIRouter(prefix="/api/revenue", tags=["revenue"])


@router.get("")
def get_revenue():
    conn = get_connection()
    cur = conn.cursor()

    # Get all invoices with new columns
    cur.execute("""
        SELECT id, odoo_id, name, type, date, due_date, amount_gross, amount_vat,
               amount_net, amount_residual, client, client_supplier_name, status
        FROM invoices ORDER BY date DESC
    """)
    invoices = []
    for row in cur.fetchall():
        d = dict(row)
        d["client"] = d.get("client") or d.get("client_supplier_name") or "Unknown"
        invoices.append(d)

    # Monthly totals
    cur.execute("""
        SELECT strftime('%Y-%m', date) as month, SUM(amount_gross) as total
        FROM invoices GROUP BY month ORDER BY month
    """)
    monthly = [{"month": row["month"], "amount": row["total"]} for row in cur.fetchall()]

    # By client
    cur.execute("""
        SELECT COALESCE(client, client_supplier_name, 'Unknown') as client_name,
               SUM(amount_gross) as total
        FROM invoices GROUP BY client_name
    """)
    by_client = {row["client_name"]: row["total"] for row in cur.fetchall()}

    # Outstanding (amount_residual > 0 or status != 'paid')
    cur.execute("""
        SELECT id, name, date, due_date, amount_gross, amount_residual, status,
               COALESCE(client, client_supplier_name, 'Unknown') as client_name
        FROM invoices WHERE status != 'paid' OR amount_residual > 0
        ORDER BY date
    """)
    outstanding = []
    for row in cur.fetchall():
        d = dict(row)
        outstanding.append({
            "name": d["name"],
            "date": d["date"],
            "due_date": d.get("due_date"),
            "amount_gross": d["amount_gross"],
            "amount_residual": d.get("amount_residual") or d["amount_gross"],
            "client": d["client_name"],
            "status": d["status"],
        })

    # Days worked current month (proxy from invoice amounts / daily rate)
    from datetime import date
    today = date.today()
    cur.execute("""
        SELECT COALESCE(SUM(amount_gross), 0) FROM invoices
        WHERE strftime('%Y-%m', date) = ?
    """, (today.strftime("%Y-%m"),))
    current_month_rev = cur.fetchone()[0] or 0.0
    days_worked = round(current_month_rev / 625) if current_month_rev > 0 else 0

    conn.close()
    return {
        "invoices": invoices,
        "monthly_totals": {m["month"]: m["amount"] for m in monthly},
        "monthly": monthly,
        "outstanding": outstanding,
        "by_client": by_client,
        "days_worked_current_month": days_worked,
    }
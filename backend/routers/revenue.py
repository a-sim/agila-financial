from fastapi import APIRouter
from database import get_connection
from models import RevenueData

router = APIRouter(prefix="/api/revenue", tags=["revenue"])

@router.get("", response_model=RevenueData)
def get_revenue():
    conn = get_connection()
    cur = conn.cursor()

    # Get all invoices
    cur.execute("SELECT * FROM invoices ORDER BY date DESC")
    invoices = [dict(row) for row in cur.fetchall()]

    # Monthly totals
    cur.execute("""
        SELECT strftime('%Y-%m', date) as month, SUM(amount_gross) as total
        FROM invoices GROUP BY month ORDER BY month
    """)
    monthly = {row["month"]: row["total"] for row in cur.fetchall()}

    # By client
    cur.execute("""
        SELECT client, SUM(amount_gross) as total FROM invoices GROUP BY client
    """)
    by_client = {row["client"]: row["total"] for row in cur.fetchall()}

    # Outstanding (not paid)
    cur.execute("SELECT * FROM invoices WHERE status != 'paid' ORDER BY date")
    outstanding = [dict(row) for row in cur.fetchall()]

    # Days worked current month (April 2026)
    from datetime import date
    today = date.today()
    days_worked = 0
    if today.year == 2026 and today.month == 4:
        days_worked = today.day

    conn.close()
    return RevenueData(
        invoices=invoices,
        monthly_totals=monthly,
        outstanding=outstanding,
        by_client=by_client,
        days_worked_current_month=days_worked
    )

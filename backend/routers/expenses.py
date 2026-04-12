from fastapi import APIRouter
from database import get_connection
from models import ExpenseData

router = APIRouter(prefix="/api/expenses", tags=["expenses"])

@router.get("", response_model=ExpenseData)
def get_expenses():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT * FROM expenses ORDER BY date DESC LIMIT 100")
    expenses = [dict(row) for row in cur.fetchall()]

    cur.execute("""
        SELECT strftime('%Y-%m', date) as month, SUM(amount) as total
        FROM expenses GROUP BY month ORDER BY month
    """)
    monthly = {row["month"]: row["total"] for row in cur.fetchall()}

    cur.execute("""
        SELECT category, SUM(amount) as total FROM expenses GROUP BY category
    """)
    by_category = {row["category"]: row["total"] for row in cur.fetchall()}

    cur.execute("SELECT SUM(amount) FROM expenses WHERE vat_recoverable = 1")
    vat_rec = cur.fetchone()[0] or 0.0

    cur.execute("SELECT SUM(amount) FROM expenses WHERE vat_recoverable = 0")
    vat_non = cur.fetchone()[0] or 0.0

    conn.close()
    return ExpenseData(
        expenses=expenses,
        monthly_totals=monthly,
        by_category=by_category,
        vat_recoverable_total=vat_rec,
        vat_non_recoverable_total=vat_non
    )

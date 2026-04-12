from fastapi import APIRouter
from database import get_connection

router = APIRouter(prefix="/api/bank", tags=["bank"])


@router.get("")
def get_bank():
    conn = get_connection()
    cur = conn.cursor()

    # Bank transactions from Odoo sync
    cur.execute(
        "SELECT * FROM bank_transactions ORDER BY date DESC LIMIT 50"
    )
    transactions = [dict(row) for row in cur.fetchall()]

    # Monthly totals
    cur.execute("""
        SELECT strftime('%Y-%m', date) as month,
               SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) as inflows,
               SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END) as outflows,
               SUM(amount) as net
        FROM bank_transactions
        GROUP BY month ORDER BY month DESC
    """)
    monthly = [dict(row) for row in cur.fetchall()]

    # Total balance (sum of all transactions)
    cur.execute("SELECT SUM(amount) FROM bank_transactions")
    balance = cur.fetchone()[0] or 0.0

    conn.close()

    return {
        "transactions": transactions,
        "monthly": monthly,
        "balance": balance,
        "count": len(transactions),
    }

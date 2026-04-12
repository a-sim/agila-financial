from fastapi import APIRouter
from database import get_connection

router = APIRouter(prefix="/api/bank", tags=["bank"])


@router.get("")
def get_bank():
    conn = get_connection()
    cur = conn.cursor()

    # Bank transactions from Odoo sync
    cur.execute("""
        SELECT id, odoo_id, date, amount, currency, partner, description,
               category, source, journal_name, match_confidence,
               reconciliation_status, matched_to_expense_id, matched_to_invoice_id
        FROM bank_transactions ORDER BY date DESC LIMIT 100
    """)
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

    # Reconciliation summary
    cur.execute("SELECT COUNT(*) FROM bank_transactions")
    total = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM bank_transactions WHERE reconciliation_status = 'matched'")
    matched = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM bank_transactions WHERE reconciliation_status = 'unmatched'")
    unmatched = cur.fetchone()[0] or 0

    conn.close()

    return {
        "transactions": transactions,
        "monthly": monthly,
        "balance": balance,
        "count": len(transactions),
        "total": total,
        "matched": matched,
        "unmatched": unmatched,
    }
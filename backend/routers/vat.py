from fastapi import APIRouter
from database import get_connection
from datetime import date

router = APIRouter(prefix="/api/vat", tags=["vat"])


@router.get("")
def get_vat():
    conn = get_connection()
    cur = conn.cursor()

    # Q1 2026: Jan-Mar
    # Output VAT = 0 for EU reverse charge invoices (all Agila invoices are B2B EU)
    cur.execute("""
        SELECT COALESCE(SUM(amount_vat), 0) FROM invoices
        WHERE date >= '2026-01-01' AND date <= '2026-03-31'
    """)
    output_vat = cur.fetchone()[0] or 0.0

    # Input VAT recoverable = expenses with vat_recoverable=1, amount * 17%
    cur.execute("""
        SELECT COALESCE(SUM(amount * 0.17), 0) FROM expenses
        WHERE date >= '2026-01-01' AND date <= '2026-03-31'
          AND vat_recoverable = 1
    """)
    input_vat = cur.fetchone()[0] or 0.0

    # Non-deductible total (vat_recoverable=0)
    cur.execute("""
        SELECT COALESCE(SUM(amount), 0) FROM expenses
        WHERE date >= '2026-01-01' AND date <= '2026-03-31'
          AND vat_recoverable = 0
    """)
    non_deductible_total = cur.fetchone()[0] or 0.0

    # By category breakdown
    cur.execute("""
        SELECT category,
               SUM(amount) as total_amount,
               SUM(CASE WHEN vat_recoverable = 1 THEN amount * 0.17 ELSE 0 END) as vat_recoverable,
               SUM(CASE WHEN vat_recoverable = 0 THEN amount ELSE 0 END) as non_deductible
        FROM expenses
        WHERE date >= '2026-01-01' AND date <= '2026-03-31'
        GROUP BY category
    """)
    by_category = []
    for row in cur.fetchall():
        by_category.append({
            "category": row["category"],
            "total_amount": row["total_amount"],
            "vat_recoverable": round(row["vat_recoverable"], 2),
            "non_deductible": row["non_deductible"],
        })

    net_vat = round(output_vat - input_vat, 2)

    # Q4 2025 for comparison
    cur.execute("""
        SELECT COALESCE(SUM(amount_vat), 0) FROM invoices
        WHERE date >= '2025-10-01' AND date <= '2025-12-31'
    """)
    q4_output = cur.fetchone()[0] or 0.0

    cur.execute("""
        SELECT COALESCE(SUM(amount * 0.17), 0) FROM expenses
        WHERE date >= '2025-10-01' AND date <= '2025-12-31'
          AND vat_recoverable = 1
    """)
    q4_input = cur.fetchone()[0] or 0.0

    # VAT returns from DB
    cur.execute("SELECT * FROM vat_returns ORDER BY year DESC, quarter DESC")
    quarters = [dict(row) for row in cur.fetchall()]

    due = date(2026, 6, 15)
    today = date.today()
    days_until = (due - today).days

    conn.close()

    return {
        "current_quarter": {
            "quarter": "Q1",
            "year": 2026,
            "output_vat": round(output_vat, 2),
            "input_vat": round(input_vat, 2),
            "net_vat": net_vat,
            "status": "pending",
            "due_date": "2026-06-15",
        },
        "previous_quarter": {
            "quarter": "Q4",
            "year": 2025,
            "output_vat": round(q4_output, 2),
            "input_vat": round(q4_input, 2),
            "net_vat": round(q4_output - q4_input, 2),
        },
        "by_category": by_category,
        "non_deductible_total": round(non_deductible_total, 2),
        "days_until_deadline": days_until,
        "quarters": quarters,
    }

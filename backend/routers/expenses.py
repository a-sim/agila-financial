from fastapi import APIRouter
from database import get_connection

router = APIRouter(prefix="/api/expenses", tags=["expenses"])

VAT_RATES = {
    'restaurant': 0.17,
    'hotel': 0.03,
    'travel': 0.17,
    'flight': 0.17,
    'taxi': 0.17,
    'software': 0.17,
    'subscription': 0.17,
    'office': 0.17,
    'supplies': 0.17,
    'professional': 0.17,
    'professional_services': 0.17,
    'other': 0.17,
}

NON_RECOVERABLE = {'restaurant'}


@router.get("")
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

    # Category breakdown with VAT recovery info
    cur.execute("""
        SELECT category,
               SUM(amount) as total,
               SUM(amount_vat) as total_vat,
               SUM(vat_recoverable) as total_recoverable
        FROM expenses GROUP BY category
    """)
    by_category = {}
    categories = []
    total_recoverable = 0.0
    total_non_recoverable = 0.0

    for row in cur.fetchall():
        cat = row["category"]
        total = row["total"] or 0.0
        vat = row["total_vat"] or 0.0
        recoverable = row["total_recoverable"] or 0.0

        # Fallback for pre-migration data
        if vat == 0 and total > 0:
            rate = VAT_RATES.get(cat, 0.17)
            vat = round(total * rate, 2)
            if cat not in NON_RECOVERABLE:
                recoverable = vat
            else:
                recoverable = 0.0

        non_recoverable = round(vat - recoverable, 2)
        total_recoverable += recoverable
        total_non_recoverable += non_recoverable

        by_category[cat] = round(total, 2)
        categories.append({
            "category": cat,
            "total": round(total, 2),
            "vat_charged": round(vat, 2),
            "vat_recoverable": round(recoverable, 2),
            "non_deductible": round(non_recoverable, 2),
        })

    conn.close()
    return {
        "expenses": expenses,
        "monthly_totals": monthly,
        "by_category": by_category,
        "categories": categories,
        "vat_recoverable_total": round(total_recoverable, 2),
        "vat_non_recoverable_total": round(total_non_recoverable, 2),
    }

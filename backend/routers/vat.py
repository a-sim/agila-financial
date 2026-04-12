from fastapi import APIRouter
from database import get_connection
from datetime import date

router = APIRouter(prefix="/api/vat", tags=["vat"])

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

    # Input VAT per category using actual vat_rate and recoverable columns
    cur.execute("""
        SELECT category,
               SUM(amount) as total_amount,
               SUM(amount_vat) as total_vat,
               SUM(vat_recoverable) as total_recoverable
        FROM expenses
        WHERE date >= '2026-01-01' AND date <= '2026-03-31'
        GROUP BY category
    """)

    by_category = []
    total_input_vat = 0.0
    total_non_recoverable_vat = 0.0
    for row in cur.fetchall():
        cat = row["category"]
        total_amount = row["total_amount"] or 0.0
        total_vat = row["total_vat"] or 0.0
        recoverable = row["total_recoverable"] or 0.0

        # If vat columns are 0 (pre-migration data), calculate from rates
        if total_vat == 0 and total_amount > 0:
            rate = VAT_RATES.get(cat, 0.17)
            total_vat = round(total_amount * rate, 2)
            if cat not in NON_RECOVERABLE:
                recoverable = total_vat
            else:
                recoverable = 0.0

        non_recoverable = round(total_vat - recoverable, 2)
        total_input_vat += recoverable
        total_non_recoverable_vat += non_recoverable

        by_category.append({
            "category": cat,
            "total_amount": round(total_amount, 2),
            "vat_charged": round(total_vat, 2),
            "vat_recoverable": round(recoverable, 2),
            "non_recoverable": round(non_recoverable, 2),
            "vat_rate": VAT_RATES.get(cat, 0.17),
            "is_recoverable": cat not in NON_RECOVERABLE,
        })

    input_vat = round(total_input_vat, 2)
    net_vat = round(output_vat - input_vat, 2)

    # Q4 2025 for comparison
    cur.execute("""
        SELECT COALESCE(SUM(amount_vat), 0) FROM invoices
        WHERE date >= '2025-10-01' AND date <= '2025-12-31'
    """)
    q4_output = cur.fetchone()[0] or 0.0

    cur.execute("""
        SELECT COALESCE(SUM(vat_recoverable), 0) FROM expenses
        WHERE date >= '2025-10-01' AND date <= '2025-12-31'
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
            "input_vat": input_vat,
            "net_vat": net_vat,
            "status": "pending",
            "due_date": "2026-06-15",
            "days_until_deadline": days_until,
        },
        "previous_quarter": {
            "quarter": "Q4",
            "year": 2025,
            "output_vat": round(q4_output, 2),
            "input_vat": round(q4_input, 2),
            "net_vat": round(q4_output - q4_input, 2),
        },
        "by_category": by_category,
        "non_recoverable_total": round(total_non_recoverable_vat, 2),
        "days_until_deadline": days_until,
        "quarters": quarters,
    }

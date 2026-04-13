from fastapi import APIRouter, Query
from database import get_connection
from datetime import date, datetime
import math

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

# Luxembourg VAT deadline: 15th of the 2nd month after quarter end
QUARTER_DEADLINES = {
    ("Q1", 2026): "2026-06-15",
    ("Q2", 2026): "2026-09-15",
    ("Q3", 2026): "2026-12-15",
    ("Q4", 2025): "2026-03-15",
    ("Q4", 2026): "2027-03-15",
}

QUARTER_MONTHS = {
    "Q1": (1, 3),
    "Q2": (4, 6),
    "Q3": (7, 9),
    "Q4": (10, 12),
}


def get_current_quarter():
    today = date.today()
    m = today.month
    q = (m - 1) // 3 + 1
    return f"Q{q}", today.year


def quarter_dates(quarter, year):
    """Return (start_date, end_date) for a quarter."""
    q = QUARTER_MONTHS[quarter]
    start = date(year, q[0], 1)
    # Last day of end month
    end_day = 31 if q[1] in (1, 3, 5, 7, 8, 10, 12) else 30 if q[1] in (4, 6, 9, 11) else 28
    # Feb leap year fix not needed for 2025-2026 range
    end = date(year, q[1], end_day)
    return start, end


def quarter_start_month(q_str):
    return {"Q1": 1, "Q2": 4, "Q3": 7, "Q4": 10}[q_str]


def detect_available_quarters(conn):
    """Return sorted list of (quarter, year) from min data quarter to current+1."""
    cur = conn.cursor()
    cur.execute("SELECT MIN(date) FROM expenses UNION SELECT MIN(date) FROM invoices")
    rows = cur.fetchall()
    all_dates = [row[0] for row in rows if row[0]]

    cq, cy = get_current_quarter()
    # Generate quarters from Q4 2025 to current+1
    start_year, start_q = 2025, "Q4"
    q_order = ["Q1", "Q2", "Q3", "Q4"]
    quarters = []
    y, q = start_year, start_q
    while (y < cy) or (y == cy and q_order.index(q) <= q_order.index(cq)):
        quarters.append((q, y))
        # advance
        if q == "Q4":
            y, q = y + 1, "Q1"
        else:
            q = q_order[q_order.index(q) + 1]
    # Always include current
    if (cq, cy) not in quarters:
        quarters.append((cq, cy))
    return quarters


def compute_quarter_vat(conn, quarter, year):
    """Compute VAT figures for a given quarter."""
    cur = conn.cursor()
    start, end = quarter_dates(quarter, year)

    # Output VAT from invoices
    cur.execute("""
        SELECT COALESCE(SUM(amount_vat), 0) FROM invoices
        WHERE date >= ? AND date <= ?
    """, (start.isoformat(), end.isoformat()))
    output_vat = cur.fetchone()[0] or 0.0

    # Input VAT per category
    cur.execute(f"""
        SELECT category,
               SUM(amount) as total_amount,
               SUM(amount_vat) as total_vat,
               SUM(vat_recoverable) as total_recoverable
        FROM expenses
        WHERE date >= '{start.isoformat()}' AND date <= '{end.isoformat()}'
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

    return {
        "output_vat": round(output_vat, 2),
        "input_vat": input_vat,
        "net_vat": net_vat,
        "by_category": by_category,
        "non_recoverable_total": round(total_non_recoverable_vat, 2),
    }


@router.get("")
def get_vat(
    quarter: str = Query(default=None, description="Quarter to view (Q1-Q4)"),
    year: int = Query(default=None, description="Year"),
):
    conn = get_connection()
    cur = conn.cursor()

    # Default to current quarter
    cq, cy = get_current_quarter()
    if quarter is None:
        quarter = cq
    if year is None:
        year = cy

    quarter = quarter.upper()
    if quarter not in ("Q1", "Q2", "Q3", "Q4"):
        quarter = cq
    if year is None:
        year = cy

    # Available quarters
    available = detect_available_quarters(conn)

    # Current quarter data
    qdata = compute_quarter_vat(conn, quarter, year)

    # Previous quarter (for comparison)
    prev_quarter_idx = available.index((quarter, year)) - 1 if (quarter, year) in available else -1
    prev_quarter_data = None
    if prev_quarter_idx >= 0:
        pq, py = available[prev_quarter_idx]
        prev = compute_quarter_vat(conn, pq, py)
        prev_quarter_data = {
            "quarter": pq,
            "year": py,
            "output_vat": prev["output_vat"],
            "input_vat": prev["input_vat"],
            "net_vat": prev["net_vat"],
        }

    # VAT returns from DB (for filed returns tracking)
    cur.execute("SELECT * FROM vat_returns ORDER BY year DESC, quarter DESC")
    vat_returns = [dict(row) for row in cur.fetchall()]

    # Deadline
    due_str = QUARTER_DEADLINES.get((quarter, year), f"{year + 1}-06-15" if quarter == "Q1" else f"{year}-09-15")
    due = datetime.strptime(due_str, "%Y-%m-%d").date()
    today = date.today()
    days_until = (due - today).days

    conn.close()

    return {
        "current_quarter": {
            "quarter": quarter,
            "year": year,
            **qdata,
            "status": "pending",
            "due_date": due_str,
            "days_until_deadline": days_until,
        },
        "previous_quarter": prev_quarter_data,
        "available_quarters": [{"quarter": q, "year": y} for q, y in available],
        "by_category": qdata["by_category"],
        "non_recoverable_total": qdata["non_recoverable_total"],
        "vat_returns": vat_returns,
        "days_until_deadline": days_until,
    }

from fastapi import APIRouter
from database import get_connection
from models import VATData
from datetime import date

router = APIRouter(prefix="/api/vat", tags=["vat"])

@router.get("", response_model=VATData)
def get_vat():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT * FROM vat_returns ORDER BY year DESC, quarter DESC")
    quarters = [dict(row) for row in cur.fetchall()]

    # Current quarter = Q1 2026
    cur.execute("""
        SELECT * FROM vat_returns WHERE year = 2026 AND quarter = 1
    """)
    row = cur.fetchone()
    current = dict(row) if row else {
        "quarter": 1, "year": 2026,
        "output_vat": 5130.93,
        "input_vat": 850.0,
        "net_vat": 4280.93,
        "status": "pending",
        "due_date": "2026-06-15"
    }

    due = date(2026, 6, 15)
    today = date.today()
    days_until = (due - today).days

    conn.close()
    return VATData(
        quarters=quarters,
        current_quarter=current,
        days_until_deadline=days_until
    )

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import init_db, get_connection
from backend.routers import revenue, expenses, vat, bank, documents
from models import SummaryKPIs

app = FastAPI(title="Agila Financial Dashboard", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
static_path = Path(__file__).parent.parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

app.include_router(revenue.router)
app.include_router(expenses.router)
app.include_router(vat.router)
app.include_router(bank.router)
app.include_router(documents.router)

@app.on_event("startup")
def startup():
    init_db()

@app.get("/api/summary", response_model=SummaryKPIs)
def get_summary():
    conn = get_connection()
    cur = conn.cursor()

    today_year, today_month = 2026, 4  # hardcoded for now

    # Current month revenue
    cur.execute("""
        SELECT COALESCE(SUM(amount_gross), 0) FROM invoices
        WHERE strftime('%Y-%m', date) = '2026-04'
    """)
    current_month_rev = cur.fetchone()[0] or 0.0

    # Q1 2026 revenue
    cur.execute("""
        SELECT COALESCE(SUM(amount_gross), 0) FROM invoices
        WHERE strftime('%Y-%m', date) >= '2026-01'
          AND strftime('%Y-%m', date) <= '2026-03'
    """)
    q1_rev = cur.fetchone()[0] or 0.0

    # Q1 2026 expenses
    cur.execute("""
        SELECT COALESCE(SUM(amount), 0) FROM expenses
        WHERE strftime('%Y-%m', date) >= '2026-01'
          AND strftime('%Y-%m', date) <= '2026-03'
    """)
    q1_exp = cur.fetchone()[0] or 0.0

    # Output VAT collected (Q1 2026)
    cur.execute("""
        SELECT COALESCE(SUM(amount_vat), 0) FROM invoices
        WHERE strftime('%Y-%m', date) >= '2026-01'
          AND strftime('%Y-%m', date) <= '2026-03'
    """)
    output_vat = cur.fetchone()[0] or 0.0

    # Input VAT recoverable (Q1 2026)
    cur.execute("""
        SELECT COALESCE(SUM(amount * 0.17), 0) FROM expenses
        WHERE strftime('%Y-%m', date) >= '2026-01'
          AND strftime('%Y-%m', date) <= '2026-03'
          AND vat_recoverable = 1
    """)
    input_vat = cur.fetchone()[0] or 0.0

    # Invoiced not received
    cur.execute("""
        SELECT COALESCE(SUM(amount_gross), 0) FROM invoices WHERE status != 'paid'
    """)
    not_received = cur.fetchone()[0] or 0.0

    conn.close()

    # ATTC annual: €4000 = €333.33/month, Q1 = €1000
    attc_q1 = 1000.0

    return SummaryKPIs(
        current_month_revenue=current_month_rev,
        current_month_target=625.0 * 22,  # ~22 working days in April
        q1_2026_revenue=q1_rev,
        q1_2026_expenses=q1_exp + attc_q1,
        net_profit_estimate=q1_rev - q1_exp - attc_q1,
        output_vat_collected=output_vat,
        input_vat_recoverable=input_vat,
        net_vat_due=output_vat - input_vat,
        invoiced_not_received=not_received
    )

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "agila-financial-dashboard"}

@app.get("/")
def root():
    return {"message": "Agila Financial Dashboard API", "docs": "/docs"}

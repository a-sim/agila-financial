from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import init_db, get_connection
from backend.routers import revenue, expenses, vat, bank, documents, reconciliation

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

app = FastAPI(title="Agila Financial Dashboard", version="2.1.0")

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
app.include_router(reconciliation.router)


@app.on_event("startup")
def startup():
    init_db()


@app.get("/api/summary")
def get_summary():
    conn = get_connection()
    cur = conn.cursor()

    # Current month revenue (April 2026)
    cur.execute("""
        SELECT COALESCE(SUM(amount_gross), 0) FROM invoices
        WHERE strftime('%Y-%m', date) = '2026-04'
    """)
    current_month_rev = cur.fetchone()[0] or 0.0

    # Q1 2026 revenue
    cur.execute("""
        SELECT COALESCE(SUM(amount_gross), 0) FROM invoices
        WHERE date >= '2026-01-01' AND date <= '2026-03-31'
    """)
    q1_rev = cur.fetchone()[0] or 0.0

    # Q1 2026 expenses
    cur.execute("""
        SELECT COALESCE(SUM(amount), 0) FROM expenses
        WHERE date >= '2026-01-01' AND date <= '2026-03-31'
    """)
    q1_exp = cur.fetchone()[0] or 0.0

    # Output VAT (0 for EU reverse charge)
    cur.execute("""
        SELECT COALESCE(SUM(amount_vat), 0) FROM invoices
        WHERE date >= '2026-01-01' AND date <= '2026-03-31'
    """)
    output_vat = cur.fetchone()[0] or 0.0

    # Input VAT recoverable (Q1 2026) — use actual vat_recoverable column
    cur.execute("""
        SELECT category, SUM(amount) as total, SUM(vat_recoverable) as recoverable
        FROM expenses
        WHERE date >= '2026-01-01' AND date <= '2026-03-31'
        GROUP BY category
    """)
    input_vat = 0.0
    for row in cur.fetchall():
        rec = row["recoverable"] or 0.0
        if rec > 0:
            input_vat += rec
        else:
            # Fallback for pre-migration data
            cat = row["category"]
            total = row["total"] or 0.0
            rate = VAT_RATES.get(cat, 0.17)
            if cat not in NON_RECOVERABLE:
                input_vat += total * rate

    input_vat = round(input_vat, 2)

    # Outstanding invoices — use amount_residual if available
    cur.execute("SELECT * FROM invoices WHERE status != 'paid'")
    outstanding_rows = cur.fetchall()
    not_received = 0.0
    outstanding_invoices = []
    for row in outstanding_rows:
        d = dict(row)
        residual = d.get("amount_residual") or d.get("amount_gross") or 0
        not_received += residual
        outstanding_invoices.append({
            "name": d.get("name"),
            "client": d.get("client") or d.get("client_supplier_name"),
            "amount_gross": d.get("amount_gross"),
            "amount_residual": residual,
            "due_date": d.get("due_date"),
        })

    # Reconciliation health from reconciliation_data
    cur.execute("SELECT COUNT(*) FROM reconciliation_data")
    recon_total = cur.fetchone()[0] or 0

    cur.execute("""
        SELECT COUNT(*) FROM reconciliation_data
        WHERE match_status IN ('MATCHED', 'MATCHED (IMAGE)', 'NO RECEIPT NEEDED')
    """)
    recon_matched = cur.fetchone()[0] or 0

    recon_pct = round((recon_matched / recon_total * 100), 1) if recon_total > 0 else 0

    # Also check bank_transactions reconciliation
    cur.execute("SELECT COUNT(*) FROM bank_transactions")
    bank_total = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM bank_transactions WHERE reconciliation_status IN ('matched', 'reviewed')")
    bank_matched = cur.fetchone()[0] or 0

    # Unmatched count
    cur.execute("""
        SELECT COUNT(*) FROM reconciliation_data
        WHERE match_status LIKE 'MISSING%'
    """)
    unmatched_count = cur.fetchone()[0] or 0

    conn.close()

    # ATTC annual retainer: ~1000/quarter
    attc_q1 = 1000.0

    return {
        "current_month_revenue": current_month_rev,
        "current_month_target": 625.0 * 22,
        "q1_2026_revenue": q1_rev,
        "q1_2026_expenses": q1_exp + attc_q1,
        "net_profit_estimate": round(q1_rev - q1_exp - attc_q1, 2),
        "output_vat_collected": output_vat,
        "input_vat_recoverable": input_vat,
        "net_vat_due": round(output_vat - input_vat, 2),
        "invoiced_not_received": round(not_received, 2),
        "outstanding_invoices": outstanding_invoices,
        "daily_rate": 625.0,
        "reconciliation_health": recon_pct,
        "bank_transactions_total": bank_total,
        "bank_transactions_matched": bank_matched,
        "unmatched_count": unmatched_count,
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "agila-financial-dashboard", "version": "2.1.0"}


@app.get("/")
def root():
    html_path = Path("/home/asimo/agila-financial-dashboard/index.html")
    return FileResponse(str(html_path))

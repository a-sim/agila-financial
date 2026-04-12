from fastapi import APIRouter, Query
from database import get_connection
from typing import Optional
import csv
import io
import sys
import subprocess
from pathlib import Path
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api", tags=["reconciliation"])

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"


# ---- Sync endpoints ----

@router.post("/sync/odoo")
def sync_odoo():
    """Trigger Odoo sync (invoices + bank entries)."""
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "sync_odoo.py")],
            capture_output=True, text=True, timeout=60,
        )
        return {
            "status": "ok" if result.returncode == 0 else "error",
            "output": result.stdout,
            "errors": result.stderr if result.returncode != 0 else None,
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "errors": "Odoo sync timed out after 60s"}
    except Exception as e:
        return {"status": "error", "errors": str(e)}


@router.post("/sync/telegram")
def sync_telegram():
    """Trigger Telegram receipts sync."""
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "sync_telegram.py")],
            capture_output=True, text=True, timeout=30,
        )
        return {
            "status": "ok" if result.returncode == 0 else "error",
            "output": result.stdout,
            "errors": result.stderr if result.returncode != 0 else None,
        }
    except Exception as e:
        return {"status": "error", "errors": str(e)}


@router.post("/reconcile")
def reconcile():
    """Trigger the reconciliation engine."""
    try:
        from backend.services.reconciliation import run_reconciliation
        summary = run_reconciliation()
        return {"status": "ok", "summary": summary}
    except Exception as e:
        return {"status": "error", "errors": str(e)}


@router.get("/sync/status")
def sync_status():
    """Get last sync timestamps from sync_log."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT source, action, records_affected, status, error, synced_at
        FROM sync_log
        ORDER BY synced_at DESC
        LIMIT 20
    """)
    logs = [dict(row) for row in cur.fetchall()]

    # Last sync per source
    cur.execute("""
        SELECT source, MAX(synced_at) as last_sync, status
        FROM sync_log
        GROUP BY source
    """)
    last_syncs = {row["source"]: {"last_sync": row["last_sync"], "status": row["status"]}
                  for row in cur.fetchall()}

    conn.close()
    return {"last_syncs": last_syncs, "recent_logs": logs}


# ---- Existing reconciliation_data endpoints ----

@router.get("/reconciliation")
def get_reconciliation(
    period: Optional[str] = None,
    match_status: Optional[str] = None,
    bank: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    amount_min: Optional[float] = None,
    amount_max: Optional[float] = None,
):
    conn = get_connection()
    cur = conn.cursor()

    # Build filtered query
    where_clauses = []
    params = []

    if period:
        where_clauses.append("period = ?")
        params.append(period)
    if match_status:
        where_clauses.append("match_status = ?")
        params.append(match_status)
    if bank:
        where_clauses.append("bank = ?")
        params.append(bank)
    if date_from:
        where_clauses.append("date >= ?")
        params.append(date_from)
    if date_to:
        where_clauses.append("date <= ?")
        params.append(date_to)
    if amount_min is not None:
        where_clauses.append("amount <= ?")  # amounts are negative (expenses)
        params.append(-amount_min)
    if amount_max is not None:
        where_clauses.append("amount >= ?")
        params.append(-amount_max)

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    # All transactions with filters
    cur.execute(
        f"SELECT * FROM reconciliation_data WHERE {where_sql} ORDER BY date DESC",
        params,
    )
    transactions = [dict(row) for row in cur.fetchall()]

    # Summary stats (unfiltered)
    cur.execute("SELECT COUNT(*) FROM reconciliation_data")
    total_count = cur.fetchone()[0]

    cur.execute(
        "SELECT COUNT(*) FROM reconciliation_data WHERE match_status IN ('MATCHED', 'MATCHED (IMAGE)')"
    )
    matched_count = cur.fetchone()[0]

    cur.execute(
        "SELECT COUNT(*) FROM reconciliation_data WHERE match_status LIKE 'MISSING%'"
    )
    missing_count = cur.fetchone()[0]

    cur.execute(
        "SELECT COUNT(*) FROM reconciliation_data WHERE match_status LIKE 'PENDING%'"
    )
    pending_count = cur.fetchone()[0]

    cur.execute(
        "SELECT COUNT(*) FROM reconciliation_data WHERE match_status LIKE 'TESLA%'"
    )
    tesla_count = cur.fetchone()[0]

    cur.execute(
        "SELECT COUNT(*) FROM reconciliation_data WHERE match_status = 'NO RECEIPT NEEDED'"
    )
    no_receipt_count = cur.fetchone()[0]

    # By status
    cur.execute(
        "SELECT match_status, COUNT(*) as cnt FROM reconciliation_data GROUP BY match_status ORDER BY cnt DESC"
    )
    by_status = {row["match_status"]: row["cnt"] for row in cur.fetchall()}

    # By bank
    cur.execute(
        "SELECT bank, COUNT(*) as cnt, SUM(amount) as total FROM reconciliation_data GROUP BY bank"
    )
    by_bank = {
        row["bank"]: {"count": row["cnt"], "total": row["total"]}
        for row in cur.fetchall()
    }

    # By period
    cur.execute(
        "SELECT period, COUNT(*) as cnt, SUM(amount) as total FROM reconciliation_data GROUP BY period"
    )
    by_period = {
        row["period"]: {"count": row["cnt"], "total": row["total"]}
        for row in cur.fetchall()
    }

    # Missing by priority
    cur.execute(
        "SELECT match_status, COUNT(*) as cnt, SUM(amount) as total FROM reconciliation_data WHERE match_status LIKE 'MISSING%' GROUP BY match_status"
    )
    missing_breakdown = {
        row["match_status"]: {"count": row["cnt"], "total": row["total"]}
        for row in cur.fetchall()
    }

    matched_pct = round((matched_count / total_count * 100), 1) if total_count > 0 else 0

    conn.close()

    return {
        "transactions": transactions,
        "filtered_count": len(transactions),
        "summary": {
            "total": total_count,
            "matched": matched_count,
            "missing": missing_count,
            "pending": pending_count,
            "tesla": tesla_count,
            "no_receipt_needed": no_receipt_count,
            "matched_pct": matched_pct,
        },
        "by_status": by_status,
        "by_bank": by_bank,
        "by_period": by_period,
        "missing_breakdown": missing_breakdown,
    }


@router.get("/reconciliation/export")
def export_csv(
    period: Optional[str] = None,
    match_status: Optional[str] = None,
    bank: Optional[str] = None,
):
    conn = get_connection()
    cur = conn.cursor()

    where_clauses = []
    params = []
    if period:
        where_clauses.append("period = ?")
        params.append(period)
    if match_status:
        where_clauses.append("match_status = ?")
        params.append(match_status)
    if bank:
        where_clauses.append("bank = ?")
        params.append(bank)

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    cur.execute(
        f"SELECT period, date, bank, description, amount, currency, match_status, receipt_file, notes FROM reconciliation_data WHERE {where_sql} ORDER BY date",
        params,
    )
    rows = cur.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Period",
            "Date",
            "Bank",
            "Description",
            "Amount",
            "Currency",
            "Match Status",
            "Receipt File",
            "Notes",
        ]
    )
    for row in rows:
        writer.writerow(list(row))

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=reconciliation_export.csv"},
    )

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


@router.post("/sync/onedrive")
def sync_onedrive():
    """Trigger OneDrive receipt scan."""
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "sync_onedrive.py")],
            capture_output=True, text=True, timeout=120,
        )
        return {
            "status": "ok" if result.returncode == 0 else "error",
            "output": result.stdout,
            "errors": result.stderr if result.returncode != 0 else None,
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "errors": "OneDrive scan timed out after 120s"}
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

_BT_STATUS_MAP = {
    "matched": "MATCHED",
    "pending_review": "PENDING",
    "transfer": "NO RECEIPT NEEDED",
    "unmatched": "MISSING - MEDIUM",
}


def _iso_from_rd_date(d: Optional[str]) -> Optional[str]:
    """Convert DD.MM.YYYY (reconciliation_data format) to YYYY-MM-DD."""
    if not d:
        return None
    parts = d.split(".")
    if len(parts) == 3 and len(parts[2]) == 4:
        return f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
    return d


def _period_from_iso(iso_date: Optional[str]) -> Optional[str]:
    if not iso_date or len(iso_date) < 7:
        return None
    try:
        year = int(iso_date[:4])
        month = int(iso_date[5:7])
        return f"Q{(month - 1) // 3 + 1} {year}"
    except (ValueError, IndexError):
        return None


def _normalize_rd_row(row: dict) -> dict:
    iso = _iso_from_rd_date(row.get("date"))
    return {
        "id": f"rd-{row['id']}",
        "date": iso or row.get("date"),
        "bank": row.get("bank"),
        "description": row.get("description"),
        "amount": row.get("amount"),
        "currency": row.get("currency") or "EUR",
        "match_status": row.get("match_status"),
        "receipt_file": row.get("receipt_file"),
        "notes": row.get("notes"),
        "period": row.get("period"),
        "source_table": "reconciliation_data",
    }


def _normalize_bt_row(row: dict) -> dict:
    iso = row.get("date")
    status = _BT_STATUS_MAP.get(row.get("reconciliation_status"), "MISSING - MEDIUM")
    desc = row.get("description") or row.get("partner") or row.get("vendor_inferred") or ""
    # Revolut is the origin bank via odoo_revolut source
    bank = "Revolut"
    return {
        "id": f"bt-{row['id']}",
        "date": iso,
        "bank": bank,
        "description": desc,
        "amount": row.get("amount"),
        "currency": row.get("currency") or "EUR",
        "match_status": status,
        "receipt_file": None,
        "notes": row.get("notes"),
        "period": _period_from_iso(iso),
        "source_table": "bank_transactions",
    }


def _passes_filters(
    tx: dict,
    period: Optional[str],
    match_status: Optional[str],
    bank: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    amount_min: Optional[float],
    amount_max: Optional[float],
) -> bool:
    if period and tx.get("period") != period:
        return False
    if match_status and tx.get("match_status") != match_status:
        return False
    if bank and tx.get("bank") != bank:
        return False
    d = tx.get("date") or ""
    if date_from and d < date_from:
        return False
    if date_to and d > date_to:
        return False
    amt = tx.get("amount") or 0
    # Existing semantics: amounts are negative for expenses; amount_min/max
    # filter by absolute magnitude of expense.
    if amount_min is not None and amt > -amount_min:
        return False
    if amount_max is not None and amt < -amount_max:
        return False
    return True


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

    cur.execute("SELECT * FROM reconciliation_data")
    rd_rows = [_normalize_rd_row(dict(r)) for r in cur.fetchall()]

    cur.execute("SELECT * FROM bank_transactions")
    bt_rows = [_normalize_bt_row(dict(r)) for r in cur.fetchall()]

    all_rows = rd_rows + bt_rows
    all_rows.sort(key=lambda t: t.get("date") or "", reverse=True)

    transactions = [
        t for t in all_rows
        if _passes_filters(t, period, match_status, bank, date_from, date_to, amount_min, amount_max)
    ]

    total_count = len(all_rows)

    def _is_matched(s): return bool(s) and "MATCHED" in s
    def _is_missing(s): return bool(s) and s.startswith("MISSING")
    def _is_pending(s): return bool(s) and s.startswith("PENDING")
    def _is_tesla(s): return bool(s) and s.startswith("TESLA")
    def _is_no_receipt(s): return s == "NO RECEIPT NEEDED"

    matched_count = sum(1 for t in all_rows if _is_matched(t["match_status"]))
    missing_count = sum(1 for t in all_rows if _is_missing(t["match_status"]))
    pending_count = sum(1 for t in all_rows if _is_pending(t["match_status"]))
    tesla_count = sum(1 for t in all_rows if _is_tesla(t["match_status"]))
    no_receipt_count = sum(1 for t in all_rows if _is_no_receipt(t["match_status"]))

    by_status: dict = {}
    for t in all_rows:
        s = t["match_status"] or "?"
        by_status[s] = by_status.get(s, 0) + 1

    by_bank: dict = {}
    for t in all_rows:
        b = t["bank"] or "?"
        entry = by_bank.setdefault(b, {"count": 0, "total": 0.0})
        entry["count"] += 1
        entry["total"] += t.get("amount") or 0

    by_period: dict = {}
    for t in all_rows:
        p = t["period"] or "?"
        entry = by_period.setdefault(p, {"count": 0, "total": 0.0})
        entry["count"] += 1
        entry["total"] += t.get("amount") or 0

    missing_breakdown: dict = {}
    for t in all_rows:
        s = t["match_status"]
        if _is_missing(s):
            entry = missing_breakdown.setdefault(s, {"count": 0, "total": 0.0})
            entry["count"] += 1
            entry["total"] += t.get("amount") or 0

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

    cur.execute("SELECT * FROM reconciliation_data")
    rd_rows = [_normalize_rd_row(dict(r)) for r in cur.fetchall()]
    cur.execute("SELECT * FROM bank_transactions")
    bt_rows = [_normalize_bt_row(dict(r)) for r in cur.fetchall()]
    conn.close()

    rows = [
        t for t in (rd_rows + bt_rows)
        if _passes_filters(t, period, match_status, bank, None, None, None, None)
    ]
    rows.sort(key=lambda t: t.get("date") or "")

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
            "Source",
        ]
    )
    for t in rows:
        writer.writerow([
            t.get("period"),
            t.get("date"),
            t.get("bank"),
            t.get("description"),
            t.get("amount"),
            t.get("currency"),
            t.get("match_status"),
            t.get("receipt_file"),
            t.get("notes"),
            t.get("source_table"),
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=reconciliation_export.csv"},
    )

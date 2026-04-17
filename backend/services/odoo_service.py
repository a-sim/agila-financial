"""
Odoo service for Agila Consulting SARL
Odoo 19 SaaS: https://agila-consulting-sarl.odoo.com
Auth: uid=2 (public automation user) + API key as password
"""
import json
import logging
import os
import urllib.request
from datetime import datetime
from pathlib import Path

LOG = logging.getLogger(__name__)

ODOO_URL = "https://agila-consulting-sarl.odoo.com/jsonrpc"
DB = "agila-consulting-sarl"
API_KEY = os.environ.get("ODOO_API_KEY", "")
UID = 2  # public automation user — works with API key as password


def _call(service: str, method: str, args: list):
    """Make an Odoo JSON-RPC call."""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method": "call",
        "params": {"service": service, "method": method, "args": args},
        "id": 1
    }).encode()
    req = urllib.request.Request(
        ODOO_URL, data=payload,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    if "error" in result:
        LOG.error(f"Odoo error: {result['error']}")
        raise Exception(result["error"].get("message", str(result["error"])))
    return result.get("result", [])


def get_posted_invoices() -> list[dict]:
    """
    Fetch all posted customer invoices (out_invoice).
    Returns list of dicts with: name, date, amount_total, partner, state
    """
    fields = [
        "name", "invoice_date", "invoice_date_due", "amount_total",
        "amount_residual", "partner_id", "state",
        "invoice_line_ids", "move_type"
    ]
    records = _call("object", "execute_kw", [
        DB, UID, API_KEY,
        "account.move",
        "search_read",
        [[["move_type", "=", "out_invoice"], ["state", "=", "posted"]]],
        {"fields": fields, "order": "invoice_date desc"}
    ])
    
    invoices = []
    for r in records:
        partner = r.get("partner_id") or [0, "Unknown"]
        invoices.append({
            "name": r["name"],
            "date": r.get("invoice_date") or "",
            "due_date": r.get("invoice_date_due") or "",
            "amount_total": r.get("amount_total") or 0.0,
            "amount_residual": r.get("amount_residual") or 0.0,
            "partner": partner[1],
            "state": r.get("state") or "",
            "type": r.get("move_type") or "out_invoice",
        })
    return invoices


def get_monthly_revenue(year: int = None) -> dict:
    """
    Aggregate revenue by month.
    Returns: {"YYYY-MM": total_amount}
    """
    if year is None:
        year = datetime.now().year
    
    invoices = get_posted_invoices()
    monthly = {}
    for inv in invoices:
        if not inv["date"]:
            continue
        dt = datetime.strptime(inv["date"], "%Y-%m-%d")
        if dt.year != year:
            continue
        month_key = dt.strftime("%Y-%m")
        monthly[month_key] = monthly.get(month_key, 0) + inv["amount_total"]
    
    # Fill in missing months with 0
    from calendar import monthrange
    for m in range(1, 13):
        month_key = f"{year}-{m:02d}"
        if month_key not in monthly:
            monthly[month_key] = 0.0
    
    return monthly


def get_outstanding_invoices() -> list[dict]:
    """Get invoices with amount_residual > 0 (not fully paid)."""
    invoices = get_posted_invoices()
    return [inv for inv in invoices if inv["amount_residual"] > 0]


if __name__ == "__main__":
    invs = get_posted_invoices()
    print(f"Found {len(invs)} posted invoices")
    for inv in invs:
        print(f"  {inv['name']} | {inv['date']} | €{inv['amount_total']:.2f} | {inv['partner']} | residual: €{inv['amount_residual']:.2f}")

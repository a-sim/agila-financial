"""
Reconciliation engine: matches bank transactions to invoices and expenses.
"""
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "agila.db"


def _date_diff(d1, d2):
    """Return absolute difference in days between two date strings."""
    try:
        dt1 = datetime.strptime(d1, "%Y-%m-%d")
        dt2 = datetime.strptime(d2, "%Y-%m-%d")
        return abs((dt1 - dt2).days)
    except (ValueError, TypeError):
        return 999


def _fuzzy_match(s1, s2):
    """Simple fuzzy match: check if any word in s1 appears in s2 or vice versa."""
    if not s1 or not s2:
        return False
    words1 = set(s1.lower().split())
    words2 = set(s2.lower().split())
    stopwords = {"", "the", "de", "a", "an", "le", "la", "sa", "nv", "bv", "srl", "sarl", "ag", "gmbh"}
    words1 -= stopwords
    words2 -= stopwords
    return bool(words1 & words2)


def run_reconciliation(conn=None):
    """
    Match bank transactions to invoices and expenses.
    Returns summary dict.
    """
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    cur = conn.cursor()

    # Step 1: Load unmatched bank transactions
    cur.execute("""
        SELECT * FROM bank_transactions
        WHERE reconciliation_status = 'unmatched'
        ORDER BY date
    """)
    bank_txns = [dict(row) for row in cur.fetchall()]

    # Step 2: Load ALL invoices (paid ones still need bank matching)
    cur.execute("""
        SELECT * FROM invoices
        ORDER BY date
    """)
    invoices = [dict(row) for row in cur.fetchall()]

    # Step 3: Load unmatched expenses
    cur.execute("""
        SELECT * FROM expenses
        WHERE matched_to_bank_id IS NULL
        ORDER BY date
    """)
    expenses_list = [dict(row) for row in cur.fetchall()]

    matched_count = 0
    low_confidence_count = 0

    for txn in bank_txns:
        txn_amount = txn["amount"] or 0
        txn_date = txn["date"] or ""
        txn_desc = txn.get("description") or ""
        txn_partner = txn.get("partner") or ""
        best_match = None
        best_confidence = 0
        match_type = None
        match_id = None

        if txn_amount > 0:
            # Inflow: match against invoices
            for inv in invoices:
                # Use amount_gross for matching (bank receives gross)
                inv_amount = inv.get("amount_gross") or inv.get("amount_residual") or 0
                inv_date = inv.get("date") or ""

                # Amount match (within EUR 1.00 tolerance for rounding/bank fees)
                if abs(txn_amount - inv_amount) <= 1.00:
                    confidence = 0.6
                    # Date proximity bonus
                    days = _date_diff(txn_date, inv_date)
                    if days <= 30:
                        confidence += 0.15
                    if days <= 7:
                        confidence += 0.1
                    # Partner name match
                    inv_client = inv.get("client") or inv.get("client_supplier_name") or ""
                    if _fuzzy_match(txn_partner, inv_client) or _fuzzy_match(txn_desc, inv_client):
                        confidence += 0.15

                    if confidence > best_confidence:
                        best_confidence = confidence
                        best_match = inv
                        match_type = "invoice"
                        match_id = inv["id"]

        elif txn_amount < 0:
            # Outflow: match against expenses
            abs_amount = abs(txn_amount)
            for exp in expenses_list:
                # Compare gross (amount + VAT) since bank charges include VAT
                exp_net = exp.get("amount") or 0
                exp_vat = exp.get("amount_vat") or 0
                exp_gross = round(exp_net + exp_vat, 2)

                # Amount match — try gross first, then net (within EUR 0.50 tolerance)
                amount_match_gross = abs(abs_amount - exp_gross) <= 0.50
                amount_match_net = abs(abs_amount - exp_net) <= 0.50
                if not (amount_match_gross or amount_match_net):
                    continue

                confidence = 0.4
                if amount_match_gross:
                    confidence += 0.1
                exp_date = exp.get("date") or ""
                days = _date_diff(txn_date, exp_date)
                if days <= 5:
                    confidence += 0.15
                elif days <= 15:
                    confidence += 0.05
                # Vendor name fuzzy match
                exp_vendor = exp.get("vendor") or ""
                desc_text = txn_desc or txn_partner or ""
                if _fuzzy_match(desc_text, exp_vendor) or _fuzzy_match(desc_text, exp.get("notes") or ""):
                    confidence += 0.3

                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = exp
                    match_type = "expense"
                    match_id = exp["id"]

        # Apply match — threshold at 0.6 (lowered from 0.7 for better recall)
        if best_match and best_confidence >= 0.6:
            status = "matched" if best_confidence >= 0.7 else "pending_review"
            if match_type == "invoice":
                cur.execute("""
                    UPDATE bank_transactions
                    SET matched_to_invoice_id = ?, match_confidence = ?,
                        reconciliation_status = ?
                    WHERE id = ?
                """, (match_id, round(best_confidence, 2), status, txn["id"]))
                invoices = [i for i in invoices if i["id"] != match_id]
            elif match_type == "expense":
                cur.execute("""
                    UPDATE bank_transactions
                    SET matched_to_expense_id = ?, match_confidence = ?,
                        reconciliation_status = ?
                    WHERE id = ?
                """, (match_id, round(best_confidence, 2), status, txn["id"]))
                cur.execute("""
                    UPDATE expenses SET matched_to_bank_id = ? WHERE id = ?
                """, (txn["id"], match_id))
                expenses_list = [e for e in expenses_list if e["id"] != match_id]

            matched_count += 1
            if best_confidence < 0.7:
                low_confidence_count += 1

    if own_conn:
        conn.commit()
        conn.close()
    else:
        conn.commit()

    unmatched_count = len(bank_txns) - matched_count
    return {
        "total_processed": len(bank_txns),
        "matched": matched_count,
        "unmatched": unmatched_count,
        "low_confidence": low_confidence_count,
    }

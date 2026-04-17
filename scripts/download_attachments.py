#!/usr/bin/env python3
"""
Download email attachments from Agila Outlook inbox and upload to OneDrive.

Uses mcporter (microsoft-mcp) for email listing and attachment download.
Uses Graph API (requests) for OneDrive upload.

Target folders:
  - Q1: 01_Agila_Lux/01_Accounting/01_Invoices-Expenses_Agila_SHARED/01_Expenses_Incoming/2026/2026_Invoices-Receipts-Statements/2026Q1_Invoices-Receipts
  - Q2: .../2026Q2_Invoices-Receipts

Naming convention: YYYYMMDD_Vendor_AmountEUR.ext

Usage:
  python3 download_attachments.py [--dry-run] [--limit 20] [--q1-only] [--q2-only]
"""
import base64
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

# --- Config ---
DB_PATH = Path(__file__).parent.parent / "agila.db"
TOKEN_CACHE = Path.home() / ".microsoft_mcp_token_cache.json"
MCPORTER = Path.home() / ".npm-global/bin/mcporter"

ACCOUNT_ID = "87fbfc0e-dfa2-4621-aab2-319dad4e93ae.c44c0a70-24ac-4b5c-adc5-8c24d4f62e21"

# OneDrive target folders (name -> id mapping)
ONEDRIVE_FOLDERS = {
    "Q1": {
        "folder_id": "017IBDTVTNKFRJBZNNP5CKSZAULMNHDUML",
        "path": "01_Agila_Lux/01_Accounting/01_Invoices-Expenses_Agila_SHARED/01_Expenses_Incoming/2026/2026_Invoices-Receipts-Statements/2026Q1_Invoices-Receipts",
        "label": "Q1 2026",
    },
    "Q2": {
        "folder_id": "017IBDTVQXONRYF6RZM5EIF4PLJJTUV4CE",
        "path": "01_Agila_Lux/01_Accounting/01_Invoices-Expenses_Agila_SHARED/01_Expenses_Incoming/2026/2026_Invoices-Receipts-Statements/2026Q2_Invoices-Receipts",
        "label": "Q2 2026",
    },
}

# Attachment extensions we care about
INVOICE_EXTENSIONS = {".pdf", ".xml"}
RECEIPT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".heic"}

# Skip senders that are not relevant
SKIP_SENDERS = {
    "noreply", "no-reply", "notification", "auto", "donotreply",
    "linkedin", "twitter", "facebook", "instagram",
}

# Vendor-to-category mapping (lowercase)
CATEGORY_KEYWORDS = {
    "restaurant": ["cafe", "restaurant", "brasserie", "bella", "ciao", "lloyd", "coffee",
                   "osteria", "cantine", "beaulieu", "coron", "globe", "rive", "mudanza",
                   "dinner", "lunch", "meal", "pizzeria"],
    "hotel": ["agoda", "hotel", "booking", "accommodation", "yorkdesign"],
    "travel": ["luxair", "flight", "lufthansa", "sas", "uber", "taxi", "train",
              "brussels", "bio", "airplane", "aeromexico", "kiwi", "kayak"],
    "flight": ["flight", "lufthansa", "luxair", "sas", "airline", "aeromexico", "kiwi.com"],
    "software": ["anthropic", "claude", "openai", "openrouter", "zoom", "notion",
                 "subscription", "github", "aws", "digitalocean", "vercel", "netlify"],
    "subscription": ["mobile vikings", "vikings"],
    "office": ["amazon", "office", "supplies"],
    "professional": ["attc", "luxtrust", "siliconlux", "fiscoges", "advisory", "fiscoGes"],
    "taxi": ["uber", "taxi"],
    "car": ["tesla", "charging", "garage", "quaresma", "adtyres", "deleren",
            "energy", "vignette", "toll"],
    "parking": ["parking", "serviparc"],
    "other": [],
}

VAT_RATES = {
    "restaurant": 0.17, "hotel": 0.03, "travel": 0.17, "flight": 0.17,
    "software": 0.17, "subscription": 0.17, "office": 0.17,
    "professional": 0.17, "taxi": 0.17, "car": 0.17, "parking": 0.17, "other": 0.17,
}
NON_RECOVERABLE = {"restaurant"}


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def get_access_token() -> str:
    """Load access token from the mcporter token cache."""
    with open(TOKEN_CACHE) as f:
        cache = json.load(f)
    at = cache.get("AccessToken", {})
    # Find the token entry for this account + graph
    for key, val in at.items():
        if isinstance(val, dict) and "secret" in val:
            return val["secret"]
    raise RuntimeError("No access token found in token cache")


# ---------------------------------------------------------------------------
# Graph API helpers
# ---------------------------------------------------------------------------

GRAPH_BASE = "https://graph.microsoft.com/v1.0/me/drive"


def graph_put(url: str, token: str, data: bytes, content_type: str) -> dict:
    """PUT bytes to Graph API, return JSON response."""
    import requests
    resp = requests.put(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
        },
        data=data,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


GRAPH_BASE = "https://graph.microsoft.com/v1.0/me/drive"


def get_drive_item_by_name(folder_id: str, filename: str, token: str) -> Optional[dict]:
    """Check if a file already exists in a OneDrive folder."""
    import requests
    from urllib.parse import quote
    # Encode filename for URL safety
    safe_name = quote(filename, safe="")
    url = (f"{GRAPH_BASE}/items/{folder_id}/children"
           f"?$filter=name eq '{safe_name}'&$select=id,name")
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
    if resp.status_code == 200:
        data = resp.json().get("value", [])
        return data[0] if data else None
    return None


def upload_to_onedrive(folder_id: str, filename: str, content: bytes,
                       content_type: str, token: str) -> dict:
    """Upload a file to OneDrive using Graph API PUT."""
    from urllib.parse import quote
    safe_name = quote(filename, safe="")

    # Check if file already exists
    existing = get_drive_item_by_name(folder_id, filename, token)
    if existing:
        # Update existing file
        item_id = existing["id"]
        url = f"{GRAPH_BASE}/items/{item_id}/content"
    else:
        # Create new file — note: use /me/drive/items/{folder_id}:/{name}:/content
        url = f"{GRAPH_BASE}/items/{folder_id}:/{safe_name}:/content"

    return graph_put(url, token, content, content_type)


# ---------------------------------------------------------------------------
# mcporter helpers
# ---------------------------------------------------------------------------

def mcporter_call(tool: str, args: dict) -> dict:
    """Call mcporter tool, parse output correctly."""
    result = subprocess.run(
        [str(MCPORTER), "call", f"microsoft.{tool}",
         "--args", json.dumps(args)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"mcporter error: {result.stderr[:300]}")
    raw = result.stdout.strip()
    # mcporter may wrap in {content: [{type:"text", text:"..."}]}
    try:
        wrapped = json.loads(raw)
        if isinstance(wrapped, dict) and "content" in wrapped:
            inner = wrapped["content"]
            if isinstance(inner, list) and len(inner) > 0:
                text = inner[0].get("text", raw)
                return json.loads(text)
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def list_emails_with_attachments(limit: int = 50) -> list:
    """List emails that have attachments from inbox."""
    result = mcporter_call("list_emails", {
        "account_id": ACCOUNT_ID,
        "folder": "inbox",
        "limit": limit,
        "include_body": False,
    })
    if isinstance(result, list):
        return [e for e in result if e.get("hasAttachments")]
    return []


def get_email_with_attachments(email_id: str) -> dict:
    """Get full email details including attachments list."""
    return mcporter_call("get_email", {
        "account_id": ACCOUNT_ID,
        "email_id": email_id,
    })


def download_attachment(email_id: str, attachment_id: str, save_path: str) -> dict:
    """Download an attachment using mcporter."""
    return mcporter_call("get_attachment", {
        "account_id": ACCOUNT_ID,
        "email_id": email_id,
        "attachment_id": attachment_id,
        "save_path": save_path,
    })


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def extract_amount_from_pdf(pdf_path: Path) -> Optional[float]:
    """
    Extract EUR/MXN amount from a PDF using pdftotext (preferred) or strings fallback.
    Returns the most likely total/invoice amount, or None if not found.
    """
    import subprocess as _sub

    def _parse_float(cleaned: str) -> Optional[float]:
        """Parse a string as float, handling European/US number formats."""
        if not cleaned:
            return None
        # Remove thousands separators: 1.234,56 or 1,234.56
        if "," in cleaned and "." in cleaned:
            if cleaned.rfind(",") > cleaned.rfind("."):
                # European: 1.234,56 -> 1234.56
                cleaned = cleaned.replace(".", "").replace(",", ".")
            else:
                # US: 1,234.56 -> 1234.56
                cleaned = cleaned.replace(",", "")
        elif "," in cleaned:
            # Could be decimal comma: 123,45 -> 123.45
            # or thousands sep: 1.234 -> 1234
            parts = cleaned.rsplit(",", 1)
            if len(parts[1]) == 2:
                cleaned = cleaned.replace(",", ".")
            else:
                cleaned = cleaned.replace(".", "").replace(",", "")
        try:
            val = float(cleaned)
            return val if 0 < val < 1_000_000 else None
        except ValueError:
            return None

    text = ""
    # Try pdftotext first (much better for text extraction)
    try:
        result = _sub.run(
            ["pdftotext", str(pdf_path), "-"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            text = result.stdout
    except FileNotFoundError:
        pass  # pdftotext not available, fall back to strings

    if not text.strip():
        # Fallback to strings for image/scanned PDFs
        try:
            result = _sub.run(
                ["strings", str(pdf_path)],
                capture_output=True, text=True, timeout=10,
            )
            text = result.stdout
        except Exception:
            pass

    if not text:
        return None

    # Priority patterns: look for totals/importe first
    priority_patterns = [
        # Spanish
        (r"Importe\s*Total[:\s]*\n?\s*([\d.,]+)", 1),
        (r"Total\s*a\s*Pagar[:\s]*([\d.,]+)", 1),
        (r"Total[:\s]+([\d.,]+)\s*(?:EUR|€)", 1),
        (r"(?:Total|total)[:\s]+([\d.,]+)", 1),
        # French
        (r"Montant\s*Total[:\s]*([\d.,]+)", 1),
        (r"Total\s*HT[:\s]*([\d.,]+)", 1),
        (r"Net\s*a\s*Payer[:\s]*([\d.,]+)", 1),
        # English / generic
        (r"Amount\s*Due[:\s]*([\d.,]+)", 1),
        (r"Invoice\s*Total[:\s]*([\d.,]+)", 1),
        (r"Grand\s*Total[:\s]*([\d.,]+)", 1),
        (r"(?:^|\n)([\d.,]+\d)\s*$", 1),  # Last number on line
    ]

    # Secondary patterns: any currency amount
    currency_patterns = [
        (r"MXN\s+([\d.,]+\d)", 1),
        (r"USD\s+([\d.,]+\d)", 1),
        (r"EUR\s+([\d.,]+\d)", 1),
        (r"([\d.,]+\d)\s*(?:MXN|USD)", 1),
        (r"€\s*([\d.,]+\d)", 1),
        (r"([\d.,]+\d)\s*€", 1),
    ]

    amounts = []

    # Try priority patterns first
    for pat, _ in priority_patterns:
        matches = re.findall(pat, text, re.MULTILINE | re.IGNORECASE)
        for m in matches:
            val = _parse_float(m.strip())
            if val:
                amounts.append(val)

    # Try currency patterns if no priority matches
    if not amounts:
        for pat, _ in currency_patterns:
            matches = re.findall(pat, text, re.MULTILINE | re.IGNORECASE)
            for m in matches:
                val = _parse_float(m.strip())
                if val:
                    amounts.append(val)

    if amounts:
        # Return the largest (most likely the total)
        return max(amounts)
    return None


def infer_category(vendor: str, sender_domain: str, subject: str) -> str:
    """Infer expense category from vendor/domain/subject."""
    text = f"{vendor} {sender_domain} {subject}".lower()
    best_cat = "other"
    best_matches = 0
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if cat == "other":
            continue
        matches = sum(1 for kw in keywords if kw.lower() in text)
        if matches > best_matches:
            best_matches = matches
            best_cat = cat
    return best_cat


def get_quarter_from_date(date_str: str) -> str:
    """Return Q1 or Q2 based on date string (YYYY-MM-DD or similar)."""
    try:
        if date_str:
            month = int(date_str[5:7]) if "-" in date_str else 1
            return "Q1" if month <= 3 else "Q2"
    except (ValueError, IndexError):
        pass
    return "Q2"  # default to Q2 for April onwards


def make_filename(date_str: str, vendor: str, amount: float, ext: str) -> str:
    """Build standardized filename: YYYYMMDD_Vendor_AmountEUR.ext"""
    safe_vendor = re.sub(r"[^\w\-]", "_", vendor)[:30]
    if date_str:
        # Accept YYYY-MM-DD or YYYYMMDD
        if "-" in date_str:
            ds = date_str.replace("-", "")
        else:
            ds = date_str[:8]
    else:
        ds = datetime.now().strftime("%Y%m%d")
    amount_str = f"{amount:.2f}".replace(".", "")
    return f"{ds}_{safe_vendor}_{amount_str}EUR{ext}"


def clean_filename(s: str) -> str:
    """Remove or replace characters that are unsafe for filenames/OneDrive."""
    s = re.sub(r'[<>:"/\\|?*]', "_", s)
    return re.sub(r"_{2,}", "_", s).strip("_")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def update_expense_from_attachment(expense_id: int, amount: float,
                                   onedrive_id: str, onedrive_path: str,
                                   filename: str, conn: sqlite3.Connection):
    """Update an expense record with downloaded attachment info."""
    cur = conn.cursor()
    cur.execute("""
        UPDATE expenses SET
            amount = ?,
            amount_vat = ROUND(amount * vat_rate, 2),
            onedrive_id = ?,
            onedrive_path = ?,
            notes = COALESCE(notes, '') || ?,
            status = 'pending'
        WHERE id = ?
    """, (amount, onedrive_id, onedrive_path,
          f" | File: {filename}", expense_id))
    conn.commit()


def insert_new_expense(date_str: str, amount: float, category: str,
                       vendor: str, description: str, filename: str,
                       onedrive_id: str, onedrive_path: str,
                       conn: sqlite3.Connection) -> int:
    """Insert a new expense from a downloaded attachment."""
    vat_rate = VAT_RATES.get(category, 0.17)
    amount_vat = round(amount * vat_rate, 2)
    vat_recoverable = 0.0 if category in NON_RECOVERABLE else amount_vat
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO expenses (date, amount, amount_vat, category, vendor,
            description, vat_rate, vat_recoverable, onedrive_id, onedrive_path,
            source, notes, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'email_attachment', ?, 'pending')
    """, (date_str, amount, amount_vat, category, vendor, description,
          vat_rate, vat_recoverable, onedrive_id, onedrive_path,
          f"File: {filename}"))
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def process_email(email: dict, token: str, attachments_dir: Path,
                  dry_run: bool = False) -> list:
    """Process a single email: download attachments, upload to OneDrive."""
    email_id = email["id"]
    subject = email.get("subject", "Unknown")
    from_addr = email.get("from", {}).get("emailAddress", {})
    sender_name = from_addr.get("name", "Unknown")
    sender_email = from_addr.get("address", "")
    sender_domain = sender_email.split("@")[-1] if sender_email else ""
    received = email.get("receivedDateTime", "")[:10]

    print(f"\n  Email: {subject[:60]}")
    print(f"  From: {sender_name} <{sender_email}>")
    print(f"  Date: {received}")

    # Skip certain senders
    if any(skip in sender_domain.lower() for skip in SKIP_SENDERS):
        print(f"  SKIP: sender domain in skip list")
        return []

    # Get full email with attachments list
    full_email = get_email_with_attachments(email_id)
    attachments = full_email.get("attachments", [])
    if not attachments:
        print(f"  No attachments found")
        return []

    print(f"  Attachments: {len(attachments)}")
    results = []

    for att in attachments:
        att_id = att.get("id")
        att_name = att.get("name", "unknown")
        att_size = att.get("size", 0)
        att_content_type = att.get("contentType", "application/octet-stream")

        ext = Path(att_name).suffix.lower()
        if ext not in INVOICE_EXTENSIONS and ext not in RECEIPT_EXTENSIONS:
            print(f"    SKIP: {att_name} (not a receipt/invoice type)")
            continue

        # Download to temp location
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp_path = Path(tmp.name)

        print(f"    Downloading: {att_name} ({att_size} bytes)")
        if not dry_run:
            try:
                download_attachment(email_id, att_id, str(tmp_path))
            except Exception as e:
                print(f"    ERROR downloading: {e}")
                continue

            # Extract amount from PDF
            amount = None
            if ext == ".pdf":
                amount = extract_amount_from_pdf(tmp_path)
                if amount:
                    print(f"    Amount extracted from PDF: EUR {amount:.2f}")

        # Determine vendor and category
        vendor = sender_name or sender_domain or "Unknown"
        # Clean vendor name
        vendor_clean = re.sub(r"[^\w\s\-]", "", vendor).strip()[:30]
        category = infer_category(vendor_clean, sender_domain, subject)

        # Determine quarter from email date
        quarter = get_quarter_from_date(received)
        folder_info = ONEDRIVE_FOLDERS[quarter]

        if amount is None:
            amount = 0.0

        filename = make_filename(received, vendor_clean, amount, ext)
        filename = clean_filename(filename)

        # Save to local attachments directory
        local_dest = attachments_dir / filename
        if not dry_run:
            import shutil
            shutil.copy2(tmp_path, local_dest)
            tmp_path.unlink()  # clean up temp file

        print(f"    Local: {local_dest}")

        # Upload to OneDrive
        if not dry_run:
            try:
                file_content = local_dest.read_bytes()
                result = upload_to_onedrive(
                    folder_info["folder_id"], filename,
                    file_content, att_content_type, token
                )
                onedrive_id = result.get("id", "")
                onedrive_web_url = result.get("webUrl", "")
                print(f"    OneDrive: {onedrive_web_url}")
                results.append({
                    "filename": filename,
                    "email_subject": subject,
                    "sender": sender_name,
                    "amount": amount,
                    "category": category,
                    "quarter": quarter,
                    "onedrive_id": onedrive_id,
                    "onedrive_path": folder_info["path"] + "/" + filename,
                    "date": received,
                })
            except Exception as e:
                print(f"    OneDrive upload ERROR: {e}")
                # Still save locally even if upload failed
                results.append({
                    "filename": filename,
                    "email_subject": subject,
                    "sender": sender_name,
                    "amount": amount,
                    "category": category,
                    "quarter": quarter,
                    "onedrive_id": "",
                    "onedrive_path": str(local_dest),
                    "date": received,
                    "upload_error": str(e),
                })

    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Download Agila email attachments")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually download or upload")
    parser.add_argument("--limit", type=int, default=50, help="Max emails to scan")
    parser.add_argument("--q1-only", action="store_true", help="Only Q1 folder")
    parser.add_argument("--q2-only", action="store_true", help="Only Q2 folder")
    args = parser.parse_args()

    print("=" * 60)
    print("Agila Email Attachment Downloader")
    print("=" * 60)

    # Ensure attachments directory exists
    attachments_dir = Path(__file__).parent.parent / "data" / "attachments"
    attachments_dir.mkdir(parents=True, exist_ok=True)
    print(f"Attachments dir: {attachments_dir}")

    # Load token
    print("Loading access token...")
    try:
        token = get_access_token()
        print("Token loaded OK")
    except Exception as e:
        print(f"FATAL: {e}")
        sys.exit(1)

    # List emails with attachments
    print(f"\nFetching inbox emails (limit={args.limit})...")
    emails = list_emails_with_attachments(limit=args.limit)
    print(f"Found {len(emails)} emails with attachments")

    # Filter by quarter if specified
    if args.q1_only:
        emails = [e for e in emails if e.get("receivedDateTime", "")[:7] <= "2026-03"]
    elif args.q2_only:
        emails = [e for e in emails if e.get("receivedDateTime", "")[:7] >= "2026-04"]

    # Process each email
    all_results = []
    for email in emails:
        try:
            results = process_email(email, token, attachments_dir,
                                    dry_run=args.dry_run)
            all_results.extend(results)
        except Exception as e:
            print(f"  ERROR processing email: {e}")

    # Update database
    if all_results and not args.dry_run:
        print(f"\nUpdating database with {len(all_results)} attachments...")
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        for r in all_results:
            if r.get("amount", 0) > 0 and not r.get("upload_error"):
                # Check if this already exists (by onedrive_path or date+vendor+amount)
                cur = conn.cursor()
                cur.execute("""
                    SELECT id FROM expenses
                    WHERE date = ? AND vendor = ? AND amount = ?
                """, (r["date"], r["sender"], r["amount"]))
                existing = cur.fetchone()

                if existing:
                    update_expense_from_attachment(
                        existing["id"], r["amount"],
                        r["onedrive_id"], r["onedrive_path"],
                        r["filename"], conn
                    )
                    print(f"  Updated: {r['filename']}")
                else:
                    eid = insert_new_expense(
                        r["date"], r["amount"], r["category"],
                        r["sender"], r["email_subject"],
                        r["filename"], r["onedrive_id"],
                        r["onedrive_path"], conn
                    )
                    print(f"  Inserted (id={eid}): {r['filename']}")

        conn.close()

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Done. Processed {len(all_results)} attachment(s).")
    if all_results:
        print("\nSummary:")
        for r in all_results:
            status = f"EUR {r['amount']:.2f}" if r.get('amount') else "AMOUNT Unknown"
            print(f"  {r['quarter']} | {status:>12} | {r['category']:15} | {r['filename']}")

    return all_results


if __name__ == "__main__":
    main()

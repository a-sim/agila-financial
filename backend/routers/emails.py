"""
Email Attachments Router for Agila Financial Dashboard.

POST /api/emails/attachments/download
  - Scans Agila Outlook inbox for emails with attachments
  - Downloads attachments using mcporter (microsoft-mcp)
  - Uploads to OneDrive Q1/Q2 expense folders via Graph API
  - Updates expenses table with amounts extracted from PDFs

Requires: mcporter, microsoft-mcp, requests, pdftotext
"""
import base64
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import msal
import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/emails", tags=["emails"])

# --- Config ---
DB_PATH = Path(__file__).parent.parent.parent / "agila.db"
TOKEN_CACHE = Path.home() / ".microsoft_mcp_token_cache.json"
MCPORTER = Path.home() / ".npm-global/bin/mcporter"
ATTACHMENTS_DIR = Path(__file__).parent.parent.parent / "data" / "attachments"
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)

CLIENT_ID = os.environ.get("MICROSOFT_CLIENT_ID", "")
AUTHORITY = "https://login.microsoftonline.com/common"
SCOPES = ["https://graph.microsoft.com/.default"]

ONEDRIVE_FOLDERS = {
    "Q1": {
        "folder_id": os.environ.get("ONEDRIVE_Q1_FOLDER_ID", ""),
        "path": "01_Agila_Lux/01_Accounting/01_Invoices-Expenses_Agila_SHARED/01_Expenses_Incoming/2026/2026_Invoices-Receipts-Statements/2026Q1_Invoices-Receipts",
    },
    "Q2": {
        "folder_id": os.environ.get("ONEDRIVE_Q2_FOLDER_ID", ""),
        "path": "01_Agila_Lux/01_Accounting/01_Invoices-Expenses_Agila_SHARED/01_Expenses_Incoming/2026/2026_Invoices-Receipts-Statements/2026Q2_Invoices-Receipts",
    },
}

GRAPH_BASE = "https://graph.microsoft.com/v1.0/me/drive"
INVOICE_EXTENSIONS = {".pdf", ".xml"}
RECEIPT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".heic"}
SKIP_SENDERS = {"noreply", "no-reply", "notification", "auto", "donotreply",
                "linkedin", "twitter", "facebook", "instagram"}

VAT_RATES = {
    "restaurant": 0.17, "hotel": 0.03, "travel": 0.17, "flight": 0.17,
    "software": 0.17, "subscription": 0.17, "office": 0.17,
    "professional": 0.17, "taxi": 0.17, "car": 0.17, "parking": 0.17, "other": 0.17,
}
NON_RECOVERABLE = {"restaurant"}

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
    "professional": ["attc", "luxtrust", "siliconlux", "fiscoges", "advisory"],
    "taxi": ["uber", "taxi"],
    "car": ["tesla", "charging", "garage", "quaresma", "adtyres", "deleren",
            "energy", "vignette", "toll"],
    "parking": ["parking", "serviparc"],
    "other": [],
}


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------

def get_access_token() -> str:
    """Get a valid Graph API access token, auto-refreshing via MSAL."""
    msal_cache = msal.SerializableTokenCache()
    if TOKEN_CACHE.exists():
        msal_cache.deserialize(TOKEN_CACHE.read_text())

    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY, token_cache=msal_cache)
    accounts = app.get_accounts()
    account = accounts[0] if accounts else None

    result = app.acquire_token_silent(SCOPES, account=account)
    if result and "access_token" in result:
        if msal_cache.has_state_changed:
            TOKEN_CACHE.write_text(msal_cache.serialize())
        return result["access_token"]

    # Fallback: raw cache read
    if TOKEN_CACHE.exists():
        cache = json.load(open(TOKEN_CACHE))
        at = cache.get("AccessToken", {})
        for key, val in at.items():
            if isinstance(val, dict) and "secret" in val:
                return val["secret"]

    raise HTTPException(status_code=500, detail="No access token found")


# ---------------------------------------------------------------------------
# mcporter helpers
# ---------------------------------------------------------------------------

def _mcporter_call(tool: str, args: dict) -> dict:
    result = subprocess.run(
        [str(MCPORTER), "call", f"microsoft.{tool}", "--args", json.dumps(args)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"mcporter error: {result.stderr[:300]}")
    raw = result.stdout.strip()
    try:
        wrapped = json.loads(raw)
        if isinstance(wrapped, dict) and "content" in wrapped:
            inner = wrapped["content"]
            if isinstance(inner, list) and len(inner) > 0:
                return json.loads(inner[0].get("text", raw))
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def list_emails_with_attachments(limit: int = 50) -> list:
    result = _mcporter_call("list_emails", {
        "account_id": ACCOUNT_ID,
        "folder": "inbox",
        "limit": limit,
        "include_body": False,
    })
    if isinstance(result, list):
        return [e for e in result if e.get("hasAttachments")]
    return []


def get_email_with_attachments(email_id: str) -> dict:
    return _mcporter_call("get_email", {
        "account_id": ACCOUNT_ID,
        "email_id": email_id,
    })


def download_attachment(email_id: str, attachment_id: str, save_path: str) -> dict:
    return _mcporter_call("get_attachment", {
        "account_id": ACCOUNT_ID,
        "email_id": email_id,
        "attachment_id": attachment_id,
        "save_path": save_path,
    })


# ---------------------------------------------------------------------------
# Graph API helpers
# ---------------------------------------------------------------------------

def graph_put(url: str, token: str, data: bytes, content_type: str) -> dict:
    resp = requests.put(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
        data=data, timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def upload_to_onedrive(folder_id: str, filename: str, content: bytes,
                       content_type: str, token: str) -> dict:
    from urllib.parse import quote
    safe_name = quote(filename, safe="")
    # Check if file already exists
    url_check = f"{GRAPH_BASE}/items/{folder_id}/children?$filter=name eq '{safe_name}'&$select=id,name"
    resp = requests.get(url_check, headers={"Authorization": f"Bearer {token}"}, timeout=15)
    existing_id = None
    if resp.status_code == 200:
        items = resp.json().get("value", [])
        if items:
            existing_id = items[0]["id"]

    if existing_id:
        url = f"{GRAPH_BASE}/items/{existing_id}/content"
    else:
        url = f"{GRAPH_BASE}/items/{folder_id}:/{safe_name}:/content"

    return graph_put(url, token, content, content_type)


# ---------------------------------------------------------------------------
# PDF parsing
# ---------------------------------------------------------------------------

def extract_amount_from_pdf(pdf_path: Path) -> Optional[float]:
    """Extract amount from PDF using pdftotext (preferred) or strings fallback."""

    def _parse_float(cleaned: str) -> Optional[float]:
        if not cleaned:
            return None
        if "," in cleaned and "." in cleaned:
            if cleaned.rfind(",") > cleaned.rfind("."):
                cleaned = cleaned.replace(".", "").replace(",", ".")
            else:
                cleaned = cleaned.replace(",", "")
        elif "," in cleaned:
            parts = cleaned.rsplit(",", 1)
            cleaned = cleaned.replace(".", "").replace(",", ".") if len(parts[1]) == 2 else cleaned.replace(",", "")
        try:
            val = float(cleaned)
            return val if 0 < val < 1_000_000 else None
        except ValueError:
            return None

    text = ""
    try:
        result = subprocess.run(
            ["pdftotext", str(pdf_path), "-"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            text = result.stdout
    except FileNotFoundError:
        pass

    if not text.strip():
        try:
            result = subprocess.run(
                ["strings", str(pdf_path)],
                capture_output=True, text=True, timeout=10,
            )
            text = result.stdout
        except Exception:
            pass

    if not text:
        return None

    priority_patterns = [
        r"Importe\s*Total[:\s]*\n?\s*([\d.,]+)",
        r"Total\s*a\s*Pagar[:\s]*([\d.,]+)",
        r"Montant\s*Total[:\s]*([\d.,]+)",
        r"Net\s*a\s*Payer[:\s]*([\d.,]+)",
        r"Amount\s*Due[:\s]*([\d.,]+)",
        r"Invoice\s*Total[:\s]*([\d.,]+)",
        r"Grand\s*Total[:\s]*([\d.,]+)",
        r"Total[:\s]+([\d.,]+)\s*(?:EUR|€|USD)",
        r"(?:Total|total)[:\s]+([\d.,]+)",
    ]

    currency_patterns = [
        r"MXN\s+([\d.,]+\d)",
        r"USD\s+([\d.,]+\d)",
        r"EUR\s+([\d.,]+\d)",
        r"([\d.,]+\d)\s*(?:MXN|USD|EUR)",
        r"€\s*([\d.,]+\d)",
        r"([\d.,]+\d)\s*€",
    ]

    amounts = []
    for pat in priority_patterns + currency_patterns:
        matches = re.findall(pat, text, re.MULTILINE | re.IGNORECASE)
        for m in matches:
            val = _parse_float(m.strip())
            if val:
                amounts.append(val)

    return max(amounts) if amounts else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def infer_category(vendor: str, sender_domain: str, subject: str) -> str:
    text = f"{vendor} {sender_domain} {subject}".lower()
    best_cat, best_matches = "other", 0
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if cat == "other":
            continue
        matches = sum(1 for kw in keywords if kw.lower() in text)
        if matches > best_matches:
            best_matches = matches
            best_cat = cat
    return best_cat


def get_quarter(date_str: str) -> str:
    try:
        month = int(date_str[5:7]) if date_str and "-" in date_str else 4
        return "Q1" if month <= 3 else "Q2"
    except (ValueError, IndexError):
        return "Q2"


def make_filename(date_str: str, vendor: str, amount: float, ext: str) -> str:
    safe_vendor = re.sub(r"[^\w\-]", "_", vendor).strip()[:30]
    ds = date_str.replace("-", "")[:8] if date_str else datetime.now().strftime("%Y%m%d")
    amount_str = f"{amount:.2f}".replace(".", "")
    name = f"{ds}_{safe_vendor}_{amount_str}EUR{ext}"
    return re.sub(r"_{2,}", "_", name).strip("_")


def clean_filename(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", s)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def ensure_columns():
    """Ensure onedrive_path column exists in expenses table."""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(expenses)")
    cols = {row[1] for row in cur.fetchall()}
    if "onedrive_path" not in cols:
        cur.execute("ALTER TABLE expenses ADD COLUMN onedrive_path TEXT")
        conn.commit()
    conn.close()


def insert_expense(date_str: str, amount: float, category: str,
                   vendor: str, description: str, filename: str,
                   onedrive_id: str, onedrive_path: str) -> int:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    vat_rate = VAT_RATES.get(category, 0.17)
    amount_vat = round(amount * vat_rate, 2)
    vat_rec = 0.0 if category in NON_RECOVERABLE else amount_vat
    cur.execute("""
        INSERT INTO expenses (date, amount, amount_vat, category, vendor,
            description, vat_rate, vat_recoverable, onedrive_id, onedrive_path,
            source, notes, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'email_attachment', ?, 'pending')
    """, (date_str, amount, amount_vat, category, vendor, description,
          vat_rate, vat_rec, onedrive_id, onedrive_path, f"File: {filename}"))
    conn.commit()
    conn.close()
    return cur.lastrowid


def update_expense(expense_id: int, amount: float, onedrive_id: str,
                   onedrive_path: str, filename: str):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        UPDATE expenses SET
            amount = ?,
            onedrive_id = ?,
            onedrive_path = ?,
            notes = COALESCE(notes, '') || ' | File: ' || ?,
            status = 'pending'
        WHERE id = ?
    """, (amount, onedrive_id, onedrive_path, filename, expense_id))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------

class AttachmentDownloadResponse(BaseModel):
    processed: int
    inserted: int
    errors: int
    attachments: list


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/attachments/download", response_model=AttachmentDownloadResponse)
def download_email_attachments(limit: int = 50, q1_only: bool = False, q2_only: bool = False):
    """
    Scan Agila Outlook inbox for emails with attachments,
    download PDFs/receipts, upload to OneDrive, and insert/update expenses.

    Query params:
      limit: max emails to scan (default 50)
      q1_only: only process Q1 2026 emails
      q2_only: only process Q2 2026 emails
    """
    ensure_columns()

    token = get_access_token()
    emails = list_emails_with_attachments(limit=limit)

    # Filter by quarter
    if q1_only:
        emails = [e for e in emails if e.get("receivedDateTime", "")[:7] <= "2026-03"]
    elif q2_only:
        emails = [e for e in emails if e.get("receivedDateTime", "")[:7] >= "2026-04"]

    processed = 0
    inserted = 0
    errors = 0
    attachment_results = []

    for email in emails:
        email_id = email["id"]
        subject = email.get("subject", "Unknown")
        from_addr = email.get("from", {}).get("emailAddress", {})
        sender_name = from_addr.get("name", "Unknown")
        sender_email = from_addr.get("address", "")
        sender_domain = sender_email.split("@")[-1] if sender_email else ""
        received = email.get("receivedDateTime", "")[:10]

        if any(skip in sender_domain.lower() for skip in SKIP_SENDERS):
            continue

        try:
            full_email = get_email_with_attachments(email_id)
        except Exception as e:
            errors += 1
            continue

        attachments = full_email.get("attachments", [])
        if not attachments:
            continue

        for att in attachments:
            att_id = att.get("id")
            att_name = att.get("name", "unknown")
            att_size = att.get("size", 0)
            att_content_type = att.get("contentType", "application/octet-stream")
            ext = Path(att_name).suffix.lower()

            if ext not in INVOICE_EXTENSIONS and ext not in RECEIPT_EXTENSIONS:
                continue

            processed += 1

            # Download to temp file
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp_path = Path(tmp.name)

            try:
                download_attachment(email_id, att_id, str(tmp_path))
            except Exception as e:
                errors += 1
                tmp_path.unlink(missing_ok=True)
                continue

            # Extract amount
            amount = extract_amount_from_pdf(tmp_path) if ext == ".pdf" else None
            if amount is None:
                amount = 0.0

            # Determine vendor/category
            vendor_clean = re.sub(r"[^\w\s\-]", "", sender_name).strip()[:30]
            category = infer_category(vendor_clean, sender_domain, subject)
            quarter = get_quarter(received)
            folder_info = ONEDRIVE_FOLDERS.get(quarter, ONEDRIVE_FOLDERS["Q2"])

            filename = clean_filename(make_filename(received, vendor_clean, amount, ext))

            # Save locally
            local_path = ATTACHMENTS_DIR / filename
            shutil.copy2(tmp_path, local_path)
            tmp_path.unlink(missing_ok=True)

            # Upload to OneDrive
            onedrive_id = ""
            onedrive_web_url = ""
            try:
                result = upload_to_onedrive(
                    folder_info["folder_id"], filename,
                    local_path.read_bytes(), att_content_type, token,
                )
                onedrive_id = result.get("id", "")
                onedrive_web_url = result.get("webUrl", "")
            except Exception as e:
                errors += 1
                onedrive_web_url = f"ERROR: {e}"

            onedrive_full_path = folder_info["path"] + "/" + filename

            # Insert or update DB
            if amount > 0:
                try:
                    conn = sqlite3.connect(str(DB_PATH))
                    conn.row_factory = sqlite3.Row
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT id FROM expenses WHERE date=? AND vendor=? AND amount=?",
                        (received, sender_name, amount),
                    )
                    existing = cur.fetchone()
                    conn.close()

                    if existing:
                        update_expense(existing["id"], amount, onedrive_id,
                                      onedrive_full_path, filename)
                    else:
                        eid = insert_expense(
                            received, amount, category, sender_name, subject,
                            filename, onedrive_id, onedrive_full_path,
                        )
                        inserted += 1
                except Exception as db_e:
                    errors += 1

            attachment_results.append({
                "filename": filename,
                "sender": sender_name,
                "subject": subject[:60],
                "amount": amount,
                "category": category,
                "quarter": quarter,
                "date": received,
                "onedrive_url": onedrive_web_url,
                "local_path": str(local_path),
            })

    return AttachmentDownloadResponse(
        processed=processed,
        inserted=inserted,
        errors=errors,
        attachments=attachment_results,
    )

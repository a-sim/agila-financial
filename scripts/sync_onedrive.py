#!/usr/bin/env python3
"""
Scan OneDrive accounting folders for receipt/invoice files.
Parse filenames to extract date, vendor, amount, category.
Import into the Agila financial dashboard expenses table.

Uses Microsoft Graph API via mcporter (microsoft-mcp) for OneDrive access.

OneDrive naming convention found in production:
  YYYYMMDD_Vendor_Description_AmountCUR.ext
  Examples:
    20260212_BellaCiao_Luxembourg_37EUR.jpg
    20260204_Anthropic_Invoice-Q29IIT8N-0003.pdf
    20260115_Tesla_Luxembourg_Invoice_22EUR.pdf
    20260322_Lufthansa_LUX-BIO_220-2243811529.pdf

Folder structure:
  01_Agila_Lux/01_Accounting/01_Invoices-Expenses_Agila_SHARED/01_Expenses_Incoming/2026/2026_Invoices-Receipts-Statements/
    ├── 2026Q1_Invoices-Receipts/
    ├── 2026Q2_Invoices-Receipts/
    ├── 2026_Bank_Statements_Everest/
    └── 2026_Bank_Statements_RevolutBusiness/
  01_Agila_Lux/01_Accounting/04_Receipts-Agila/2026/ (Telegram bot uploads)
"""
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "agila.db"
ACCOUNT_ID = "87fbfc0e-dfa2-4621-aab2-319dad4e93ae.c44c0a70-24ac-4b5c-adc5-8c24d4f62e21"

# OneDrive paths to scan (relative to root)
SCAN_PATHS = [
    "01_Agila_Lux/01_Accounting/01_Invoices-Expenses_Agila_SHARED/01_Expenses_Incoming/2026/2026_Invoices-Receipts-Statements/2026Q1_Invoices-Receipts",
    "01_Agila_Lux/01_Accounting/01_Invoices-Expenses_Agila_SHARED/01_Expenses_Incoming/2026/2026_Invoices-Receipts-Statements/2026Q2_Invoices-Receipts",
    "01_Agila_Lux/01_Accounting/01_Invoices-Expenses_Agila_SHARED/01_Expenses_Incoming/2026/2026_Invoices-Receipts-Statements/2026Q3_Invoices-Receipts",
    "01_Agila_Lux/01_Accounting/04_Receipts-Agila/2026",
]

# Skip bank statements and salary slips — they're not expenses
SKIP_PATTERNS = [
    "Bank_Statement",
    "Statement",
    "Salary",
    "PaySlip",
    "VAT_Declaration",
    "TVA",
    "Reminders",
    "Parking_Reminder",
]

# File extensions to process
RECEIPT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".pdf", ".xml"}

# Category inference from vendor/description keywords
CATEGORY_KEYWORDS = {
    "restaurant": [
        "cafe", "restaurant", "brasserie", "bella", "ciao", "lloyd", "coffee",
        "osteria", "cantine", "beaulieu", "coron", "globe", "rive", "mudanza",
        "dinner", "lunch", "meal",
    ],
    "hotel": ["agoda", "hotel", "booking", "accommodation", "yorkdesign"],
    "travel": [
        "luxair", "flight", "lufthansa", "sas", "uber", "taxi", "train",
        "brussels", "bru-", "trf", "bio", "airplane",
    ],
    "flight": ["flight", "lufthansa", "luxair", "sas", "airline"],
    "software": [
        "anthropic", "claude", "openai", "openrouter", "zoom", "notion",
        "subscription", "api",
    ],
    "subscription": ["mobile vikings", "vikings"],
    "office": ["amazon", "office", "supplies"],
    "professional": ["attc", "luxtrust", "siliconlux", "fiscoges", "advisory"],
    "taxi": ["uber", "taxi"],
    "car": ["tesla", "charging", "garage", "quaresma", "adtyres", "deleren", "energy", "vignette", "toll"],
    "parking": ["parking", "serviparc"],
    "other": [],
}

# VAT rates per category
VAT_RATES = {
    "restaurant": 0.17,     # VAT charged but NOT deductible
    "hotel": 0.03,          # Super-reduced
    "travel": 0.17,
    "flight": 0.17,
    "software": 0.17,
    "subscription": 0.17,
    "office": 0.17,
    "professional": 0.17,
    "taxi": 0.17,
    "car": 0.17,
    "parking": 0.17,
    "other": 0.17,
}

NON_RECOVERABLE = {"restaurant"}


def list_onedrive_files(path):
    """Call mcporter to list files in a OneDrive path."""
    try:
        result = subprocess.run(
            [
                "mcporter", "call", "microsoft.list_files",
                "--args", json.dumps({
                    "account_id": ACCOUNT_ID,
                    "path": path,
                    "limit": 200,
                }),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"  Error listing {path}: {result.stderr[:200]}")
            return []
        items = json.loads(result.stdout)
        if isinstance(items, dict):
            # Single item returned
            return [items]
        return items
    except Exception as e:
        print(f"  Error listing {path}: {e}")
        return []


def parse_filename(filename):
    """
    Parse a receipt filename into structured data.
    Expected format: YYYYMMDD_Vendor_Description_AmountCUR.ext
    Also handles: YYYYMMDD_Vendor_Description.ext (no amount)
    """
    name = Path(filename).stem  # remove extension
    ext = Path(filename).suffix.lower()

    # Skip non-receipt files
    if ext not in RECEIPT_EXTENSIONS:
        return None

    # Skip patterns
    for skip in SKIP_PATTERNS:
        if skip.lower() in name.lower():
            return None

    # Try to parse the standard format: YYYYMMDD_Vendor_...
    match = re.match(
        r"(\d{8})_(.+?)(?:_(\d+\.?\d*)(EUR|USD|NT))?$',",
        name + "'",  # add quote to help regex end
    )
    # Simpler approach: split by underscore
    parts = name.split("_")

    # First part should be date YYYYMMDD
    date_str = parts[0] if parts else ""
    if not re.match(r"^\d{8}$", date_str):
        # Not a standard receipt filename
        return None

    # Parse date
    try:
        year = date_str[:4]
        month = date_str[4:6]
        day = date_str[6:8]
        date_iso = f"{year}-{month}-{day}"
    except (ValueError, IndexError):
        return None

    # Guard: Agila started April 2025. Any date before that from filename is likely a misread.
    if date_iso < "2025-04-01":
        if year in ("2022", "2023"):
            date_iso = "2026" + date_iso[4:]
        elif year == "2024":
            date_iso = "2025" + date_iso[4:]

    # Try to extract amount from the end (e.g., 37EUR, 22EUR)
    amount = None
    currency = "EUR"
    remaining_parts = parts[1:]

    # Check if last part contains amount+currency
    if remaining_parts:
        last = remaining_parts[-1]
        amount_match = re.match(r"^(\d+\.?\d*)(EUR|USD|GBP|NT)$", last)
        if amount_match:
            raw_amount = amount_match.group(1)
            amount = float(raw_amount)
            currency = amount_match.group(2)
            remaining_parts = remaining_parts[:-1]
            # Amount sanity: if no decimal point in raw amount and value > 500,
            # the filename likely omitted the decimal (e.g., 3122EUR = 31.22EUR).
            # Heuristic: divide by 100 and check if the result looks more plausible.
            # Only applies when the raw string has no '.' — amounts over 500 without
            # decimals are rare for expense receipts.
            if '.' not in raw_amount and amount > 500:
                candidate = amount / 100
                print(f"  WARNING: Amount {amount:.2f} has no decimal point and exceeds 500. "
                      f"Possible missing decimal — likely {candidate:.2f}. "
                      f"Importing as 0 and flagging for manual review.")
                amount = 0.0  # Flag for manual review instead of guessing

    # The rest is vendor + description
    vendor_desc = " ".join(remaining_parts) if remaining_parts else "Unknown"

    # Try to split vendor from description
    # Heuristic: first word/segment is usually the vendor
    vendor = remaining_parts[0] if remaining_parts else "Unknown"
    description = " ".join(remaining_parts[1:]) if len(remaining_parts) > 1 else ""

    return {
        "date": date_iso,
        "vendor": vendor,
        "description": description,
        "vendor_desc": vendor_desc,
        "amount": amount,
        "currency": currency,
        "filename": filename,
    }


def infer_category(vendor, description):
    """Infer expense category from vendor and description."""
    text = f"{vendor} {description}".lower()

    # Check each category's keywords
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


def scan_and_import():
    """Main: scan OneDrive folders, parse filenames, import to DB."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    total_found = 0
    total_imported = 0
    total_skipped = 0

    all_files = []

    # Scan each OneDrive path
    for path in SCAN_PATHS:
        print(f"Scanning: {path}")
        files = list_onedrive_files(path)
        print(f"  Found {len(files)} items")
        for f in files:
            if isinstance(f, dict) and f.get("type") == "file":
                all_files.append({
                    "name": f["name"],
                    "id": f["id"],
                    "size": f.get("size", 0),
                    "path": path,
                })

    print(f"\nTotal files found: {len(all_files)}")

    # Parse and import each file
    for f in all_files:
        total_found += 1
        parsed = parse_filename(f["name"])
        if not parsed:
            total_skipped += 1
            continue

        # Check if already imported (by onedrive_id)
        cur.execute("SELECT id FROM expenses WHERE onedrive_id = ?", (f["id"],))
        if cur.fetchone():
            total_skipped += 1
            continue

        # Also check by filename pattern to avoid duplicates
        cur.execute(
            "SELECT id FROM expenses WHERE date = ? AND vendor = ? AND amount = ?",
            (parsed["date"], parsed["vendor"], parsed["amount"] or 0),
        )
        if cur.fetchone():
            total_skipped += 1
            continue

        # Infer category
        category = infer_category(parsed["vendor"], parsed["description"])

        # Calculate VAT
        vat_rate = VAT_RATES.get(category, 0.17)
        amount = parsed["amount"]
        if amount is None:
            # No amount in filename — import with 0, flag for review
            amount = 0.0
            amount_vat = 0.0
            vat_recoverable = 0.0
        else:
            amount_vat = round(amount * vat_rate, 2)
            if category in NON_RECOVERABLE:
                vat_recoverable = 0.0
            else:
                vat_recoverable = amount_vat

        # Insert
        try:
            cur.execute("""
                INSERT INTO expenses (date, amount, amount_vat, category, vendor,
                    description, vat_rate, vat_recoverable, onedrive_id, source,
                    notes, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'onedrive', ?, 'pending')
            """, (
                parsed["date"],
                amount,
                amount_vat,
                category,
                parsed["vendor"],
                parsed["description"] or parsed["vendor_desc"],
                vat_rate,
                vat_recoverable,
                f["id"],
                f"File: {f['name']}" + (f" | Path: {f['path']}" if f.get("path") else ""),
            ))
            total_imported += 1
            print(f"  IMPORTED: {parsed['date']} | EUR {amount:.2f} | {category:15s} | {parsed['vendor']} | {f['name']}")
        except Exception as e:
            print(f"  ERROR importing {f['name']}: {e}")
            total_skipped += 1

    # Log sync
    cur.execute(
        "INSERT INTO sync_log (source, action, records_affected, status) VALUES (?,?,?,?)",
        ("onedrive", "scan_receipts", total_imported, "ok"),
    )

    conn.commit()
    conn.close()

    print(f"\nOneDrive scan complete:")
    print(f"  Files found: {total_found}")
    print(f"  Imported: {total_imported}")
    print(f"  Skipped (non-receipt, already imported, or no amount): {total_skipped}")
    return total_imported


if __name__ == "__main__":
    scan_and_import()

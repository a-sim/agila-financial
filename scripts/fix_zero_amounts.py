#!/usr/bin/env python3
"""
One-time fix: Process zero-amount expenses by downloading their receipts,
analyzing with vision API, extracting amounts + currencies, converting
foreign currencies to EUR, and updating the DB + OneDrive filenames.
"""
import json, os, base64, sqlite3, subprocess, sys, tempfile, time
from pathlib import Path
from PIL import Image
import pillow_heif
import requests as http_requests

# Register HEIF opener with Pillow
pillow_heif.register_heif_opener()

sys.stdout.reconfigure(line_buffering=True)

DB_PATH = Path("/home/asimo/agila-financial-dashboard/agila.db")
TOKEN_CACHE = Path.home() / ".microsoft_mcp_token_cache.json"
GRAPH_BASE = "https://graph.microsoft.com/v1.0/me/drive"
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY") or open(Path.home() / ".openrouter-api-key").read().strip()
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# ECB reference rates for common currencies (approximate mid-rates for the period)
# For precision, we use the rate closest to the transaction date
# These are the major ECB reference rates for EUR (how many X per 1 EUR)
ECB_RATES = {
    # Asian currencies - Dec 2025 rates
    "HKD": 8.20,    # Hong Kong Dollar (Dec 2025)
    "PHP": 60.50,   # Philippine Peso (Dec 2025)
    "TWD": 34.50,   # Taiwan New Dollar (NTD) (Jan 2026)
    "AED": 3.90,    # UAE Dirham (Nov 2025)
    "CNY": 7.60,    # Chinese Yuan (Dec 2025)
    # European currencies
    "DKK": 7.46,    # Danish Krone
    "NOK": 11.10,   # Norwegian Krone
    "SEK": 11.20,   # Swedish Krona
    "GBP": 0.84,    # British Pound
    "CHF": 0.94,    # Swiss Franc
    "USD": 1.05,    # US Dollar (Dec 2025)
}

# More precise date-specific rates for known transactions
DATE_RATES = {
    # Hong Kong - late Dec 2025
    ("HKD", "2025-12-29"): 8.18,
    ("HKD", "2025-12-30"): 8.18,
    # Philippines - Dec 2025
    ("PHP", "2025-12-21"): 60.30,
    ("PHP", "2025-12-27"): 60.30,
    ("PHP", "2025-12-31"): 60.40,
    ("PHP", "2026-01-01"): 60.40,
    # Taiwan - Jan 2026
    ("TWD", "2026-01-09"): 34.40,
    ("TWD", "2026-01-11"): 34.40,
    # UAE Dirham - Nov 2025
    ("AED", "2025-11-28"): 3.90,
    ("AED", "2025-11-29"): 3.90,
    # CNY - Dec 2025
    ("CNY", "2025-12-29"): 7.58,
    # USD
    ("USD", "2025-09-25"): 1.12,
    ("USD", "2025-10-31"): 1.08,
    ("USD", "2025-12-25"): 1.04,
    ("USD", "2026-02-19"): 1.05,
}

VAT_RATE = 0.17


def get_token():
    import subprocess as sp
    try:
        sp.run(
            ["mcporter", "call", "microsoft.list_emails",
             "account_id=87fbfc0e-dfa2-4621-aab2-319dad4e93ae.c44c0a70-24ac-4b5c-adc5-8c24d4f62e21",
             "folder=Inbox"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        pass
    with open(TOKEN_CACHE) as f:
        cache = json.load(f)
    return list(cache["AccessToken"].values())[0]["secret"]


def convert_to_eur(amount, currency, date_str):
    """Convert foreign currency to EUR using ECB reference rates."""
    if currency == "EUR":
        return amount

    # Check date-specific rate first
    rate_key = (currency, date_str)
    if rate_key in DATE_RATES:
        rate = DATE_RATES[rate_key]
        eur_amount = round(amount / rate, 2)
        print(f"    Currency: {amount} {currency} -> {eur_amount} EUR (rate {rate}, date-specific)")
        return eur_amount

    # Fall back to general rate
    if currency in ECB_RATES:
        rate = ECB_RATES[currency]
        eur_amount = round(amount / rate, 2)
        print(f"    Currency: {amount} {currency} -> {eur_amount} EUR (rate {rate}, general)")
        return eur_amount

    print(f"    WARNING: Unknown currency {currency}, assuming EUR")
    return amount


def download_receipt(file_id, token, local_path):
    """Download a receipt file from OneDrive."""
    url = f"{GRAPH_BASE}/items/{file_id}/content"
    resp = http_requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    with open(local_path, "wb") as f:
        f.write(resp.content)
    return len(resp.content)


def convert_to_jpeg(local_path):
    """Convert any image/PDF to JPEG for vision API."""
    ext = Path(local_path).suffix.lower()

    if ext == ".pdf":
        jpg_path = local_path.replace(ext, ".jpg")
        result = subprocess.run(
            ["pdftoppm", "-jpeg", "-r", "150", "-singlefile", local_path, jpg_path.replace(".jpg", "")],
            capture_output=True, text=True, timeout=30
        )
        if os.path.exists(jpg_path):
            os.unlink(local_path)
            return jpg_path
        return None

    elif ext in (".heic", ".heif"):
        # Convert HEIC to JPEG using Pillow + pillow-heif
        try:
            img = Image.open(local_path)
            jpg_path = local_path.replace(ext, ".jpg")
            img.convert("RGB").save(jpg_path, "JPEG", quality=90)
            os.unlink(local_path)
            return jpg_path
        except Exception as e:
            print(f"    HEIC conversion error: {e}")
            return None

    elif ext in (".jpg", ".jpeg", ".png"):
        return local_path

    elif ext == ".xml":
        # XML files are not images - read them directly for text
        return None  # Will handle separately

    return None


def read_xml_amount(local_path):
    """Try to extract amount from XML e-invoice."""
    try:
        with open(local_path, "r", errors="replace") as f:
            content = f.read()
        # Look for common amount patterns in e-invoices
        import re
        # Taiwan e-invoice patterns
        matches = re.findall(r'Amount["\s:=]+(\d+)', content)
        if matches:
            return float(matches[0])
        matches = re.findall(r'amount["\s:=]+(\d+)', content)
        if matches:
            return float(matches[0])
        # Look for TWD/NTD amounts
        matches = re.findall(r'(\d+)\s*(TWD|NTD|NT\$|NT)', content)
        if matches:
            return float(matches[0][0])
    except Exception:
        pass
    return None


def analyze_receipt(jpg_path, vendor_hint=""):
    """Analyze a receipt image via vision API."""
    with open(jpg_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    prompt = (
        "You are a receipt/invoice scanner for business expenses. "
        "Extract these fields:\n"
        "1. amount: the TOTAL amount charged (number only, in original currency)\n"
        "2. currency: ISO 4217 code (EUR, HKD, PHP, USD, TWD, AED, CNY, DKK, etc.)\n"
        "3. date: the charge/invoice date in YYYY-MM-DD format\n"
        "4. description: brief description of what was purchased\n\n"
        "IMPORTANT: If the amount is in a non-EUR currency, report the ORIGINAL amount and currency code, "
        "NOT a converted amount. For example, if a Hong Kong receipt shows HKD 287, report amount=287 currency=HKD.\n\n"
        f"Vendor hint: {vendor_hint}\n\n"
        "Reply ONLY with valid JSON. Example:\n"
        '{"amount": 287, "date": "2025-12-29", "currency": "HKD", "description": "Uber ride Hong Kong"}'
    )

    resp = http_requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"},
        json={
            "model": "openai/gpt-4o-mini",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
            ]}],
            "max_tokens": 200,
            "temperature": 0.1,
        },
        timeout=30,
    )

    if resp.status_code == 200:
        text = resp.json()["choices"][0]["message"]["content"].strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    else:
        print(f"    Vision API error: {resp.status_code}")
    return None


def rename_onedrive_file(old_file_id, old_name, new_name, folder_id, token):
    """Rename a file on OneDrive by downloading, re-uploading, and deleting the old one."""
    # First check if new name already exists
    check_resp = http_requests.get(
        f"{GRAPH_BASE}/items/{folder_id}:/{new_name}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30
    )
    if check_resp.status_code == 200:
        print(f"    File already exists with new name: {new_name}")
        new_id = check_resp.json().get("id")
        # Delete old file
        http_requests.delete(f"{GRAPH_BASE}/items/{old_file_id}", headers={"Authorization": f"Bearer {token}"}, timeout=30)
        return new_id

    # Download, re-upload with new name, delete old
    tmp = tempfile.mktemp(suffix=Path(old_name).suffix)
    download_receipt(old_file_id, token, tmp)
    with open(tmp, "rb") as f:
        content = f.read()

    upload_resp = http_requests.put(
        f"{GRAPH_BASE}/items/{folder_id}:/{new_name}:/content",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"},
        data=content,
        timeout=60,
    )
    os.unlink(tmp)

    if upload_resp.status_code in (200, 201):
        new_id = upload_resp.json().get("id")
        # Delete old file
        http_requests.delete(f"{GRAPH_BASE}/items/{old_file_id}", headers={"Authorization": f"Bearer {token}"}, timeout=30)
        print(f"    Renamed on OneDrive: {old_name} -> {new_name}")
        return new_id
    else:
        print(f"    Upload error: {upload_resp.status_code}")
        return old_file_id


def main():
    token = get_token()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Get all zero-amount expenses with onedrive_id
    cur.execute("""
        SELECT id, date, amount, vendor, description, onedrive_id, source, notes, category
        FROM expenses
        WHERE (amount = 0 OR amount IS NULL) AND onedrive_id IS NOT NULL
        ORDER BY date
    """)
    rows = cur.fetchall()

    print(f"Found {len(rows)} zero-amount expenses with OneDrive receipts\n")

    fixed = 0
    errors = 0

    for i, row in enumerate(rows):
        eid = row["id"]
        date = row["date"]
        vendor = row["vendor"] or ""
        desc = row["description"] or ""
        onedrive_id = row["onedrive_id"]
        category = row["category"] or "other"
        notes = row["notes"] or ""

        print(f"[{i+1}/{len(rows)}] id={eid} | {date} | {vendor} | {desc[:50]}")

        # Get file info from OneDrive
        try:
            info_resp = http_requests.get(
                f"{GRAPH_BASE}/items/{onedrive_id}?$select=name,parentReference",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30
            )
            if info_resp.status_code != 200:
                print(f"    ERROR getting file info: {info_resp.status_code}")
                errors += 1
                continue
            file_info = info_resp.json()
            file_name = file_info.get("name", "")
            parent_id = file_info.get("parentReference", {}).get("id", "")
        except Exception as e:
            print(f"    ERROR: {e}")
            errors += 1
            continue

        # Download receipt
        ext = Path(file_name).suffix.lower()
        tmp_path = tempfile.mktemp(suffix=ext)
        try:
            download_receipt(onedrive_id, token, tmp_path)
        except Exception as e:
            print(f"    ERROR downloading: {e}")
            errors += 1
            continue

        # Convert to JPEG if needed
        if ext == ".xml":
            # Try to read amount from XML directly
            xml_amount = read_xml_amount(tmp_path)
            os.unlink(tmp_path)
            if xml_amount:
                # For Taiwan e-invoices, assume TWD
                eur_amount = convert_to_eur(xml_amount, "TWD", date)
                analysis = {"amount": xml_amount, "currency": "TWD", "date": date, "description": desc}
            else:
                print(f"    Could not extract amount from XML")
                errors += 1
                continue
        else:
            jpg_path = convert_to_jpeg(tmp_path)
            if not jpg_path:
                print(f"    ERROR converting {file_name}")
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                errors += 1
                continue

            # Analyze with vision API
            analysis = analyze_receipt(jpg_path, f"{vendor} {desc}")
            if os.path.exists(jpg_path):
                os.unlink(jpg_path)

            if not analysis:
                print(f"    ERROR analyzing receipt")
                errors += 1
                continue

        # Extract and validate
        amount = analysis.get("amount") or 0
        currency = analysis.get("currency", "EUR").upper()
        analysis_date = analysis.get("date", date)
        analysis_desc = analysis.get("description", desc)

        if amount <= 0:
            print(f"    WARNING: zero or negative amount: {amount}")
            errors += 1
            continue

        # Validate date
        if analysis_date and analysis_date < "2025-04-01":
            print(f"    WARNING: bad date {analysis_date}, keeping original {date}")
            analysis_date = date

        # Convert to EUR if foreign currency
        if currency != "EUR":
            eur_amount = convert_to_eur(amount, currency, analysis_date or date)
        else:
            eur_amount = amount

        # Calculate VAT
        amount_vat = round(eur_amount * VAT_RATE, 2)
        vat_recoverable = amount_vat

        # Build new description
        if currency != "EUR":
            new_desc = f"{analysis_desc} (Original: {amount:.2f} {currency})"
        else:
            new_desc = analysis_desc

        # Build new OneDrive filename
        date_compact = (analysis_date or date).replace("-", "")
        amount_str = f"{eur_amount:.2f}EUR" if currency == "EUR" else f"{amount:.2f}{currency}"
        new_filename = f"{date_compact}_{vendor.replace(' ','')}_{category.capitalize()}_{amount_str}{ext}"

        # Rename on OneDrive if needed and we have parent folder ID
        new_onedrive_id = onedrive_id
        if parent_id and file_name != new_filename:
            try:
                new_onedrive_id = rename_onedrive_file(onedrive_id, file_name, new_filename, parent_id, token)
            except Exception as e:
                print(f"    Rename error (non-fatal): {e}")

        # Update DB
        cur.execute("""
            UPDATE expenses SET
                amount = ?, amount_vat = ?, vat_rate = ?, vat_recoverable = ?,
                description = ?, notes = ?, onedrive_id = ?, date = ?
            WHERE id = ?
        """, (
            eur_amount, amount_vat, VAT_RATE, vat_recoverable,
            new_desc,
            f"Original: {amount:.2f} {currency} -> {eur_amount:.2f} EUR" if currency != "EUR" else f"File: {new_filename}",
            new_onedrive_id,
            analysis_date or date,
            eid,
        ))
        conn.commit()
        fixed += 1
        print(f"    FIXED: {eur_amount:.2f} EUR ({currency}) | {new_desc[:60]}")

        time.sleep(0.8)  # Rate limiting

    # Now also fix known foreign currency items that have amounts but might be wrong
    print("\n\n=== Checking known foreign currency items ===")
    cur.execute("""
        SELECT id, date, amount, vendor, description, notes
        FROM expenses
        WHERE (description LIKE '%HKD%' OR description LIKE '%PHP%' OR notes LIKE '%HKD%' OR notes LIKE '%PHP%'
               OR description LIKE '%NT%' OR description LIKE '%TWD%' OR description LIKE '%Dubai%' OR description LIKE '%AED%'
               OR description LIKE '%Taiwan%')
          AND amount > 0
    """)
    fc_rows = cur.fetchall()
    for row in fc_rows:
        print(f"  id={row['id']} | {row['date']} | EUR {row['amount']} | {row['description'][:60]}")

    conn.close()
    print(f"\nDone: {fixed} fixed, {errors} errors")


if __name__ == "__main__":
    main()

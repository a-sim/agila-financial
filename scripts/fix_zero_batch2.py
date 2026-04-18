#!/usr/bin/env python3
"""
Batch 2: Process remaining zero-amount expenses.
Optimized for Pi: smaller images, explicit gc, batch of 10.
"""
import json, os, base64, gc, sqlite3, subprocess, sys, tempfile, time
from pathlib import Path
from PIL import Image
import pillow_heif
import requests as http_requests

pillow_heif.register_heif_opener()
sys.stdout.reconfigure(line_buffering=True)

DB_PATH = Path("/home/asimo/agila-financial-dashboard/agila.db")
TOKEN_CACHE = Path.home() / ".microsoft_mcp_token_cache.json"
GRAPH_BASE = "https://graph.microsoft.com/v1.0/me/drive"
OPENROUTER_KEY = open(Path.home() / ".openrouter-api-key").read().strip()

ECB_RATES = {
    "HKD": 8.18, "PHP": 60.40, "TWD": 34.40, "AED": 3.90,
    "CNY": 7.58, "USD": 1.05, "DKK": 7.46, "NOK": 11.10,
    "GBP": 0.84, "CHF": 0.94,
}
DATE_RATES = {
    ("USD", "2025-09-25"): 1.12, ("USD", "2025-10-31"): 1.08,
    ("USD", "2025-12-25"): 1.04, ("USD", "2026-02-19"): 1.05,
    ("AED", "2025-11-28"): 3.90, ("AED", "2025-11-29"): 3.90,
}
VAT_RATE = 0.17

def get_token():
    import subprocess as sp
    try:
        sp.run(["mcporter", "call", "microsoft.list_emails",
                 "account_id=87fbfc0e-dfa2-4621-aab2-319dad4e93ae.c44c0a70-24ac-4b5c-adc5-8c24d4f62e21",
                 "folder=Inbox"], capture_output=True, text=True, timeout=30)
    except: pass
    with open(TOKEN_CACHE) as f: cache = json.load(f)
    return list(cache["AccessToken"].values())[0]["secret"]

def convert_to_eur(amount, currency, date_str):
    if currency == "EUR": return amount
    rate = DATE_RATES.get((currency, date_str)) or ECB_RATES.get(currency)
    if rate:
        eur = round(amount / rate, 2)
        print(f"    {amount:.2f} {currency} -> {eur:.2f} EUR (rate {rate})")
        return eur
    print(f"    WARNING: unknown currency {currency}, assuming EUR")
    return amount

def process_image(filepath):
    """Convert any file to a small JPEG for vision API."""
    ext = Path(filepath).suffix.lower()
    if ext == ".pdf":
        jpg = filepath.replace(ext, ".jpg")
        subprocess.run(["pdftoppm", "-jpeg", "-r", "100", "-singlefile", filepath, jpg.replace(".jpg","")],
                      capture_output=True, timeout=30)
        os.unlink(filepath)
        if not os.path.exists(jpg): return None
        # Resize for memory
        img = Image.open(jpg)
        img.thumbnail((800, 800))
        img.save(jpg, "JPEG", quality=70)
        return jpg
    elif ext in (".heic", ".heif"):
        img = Image.open(filepath)
        img = img.convert("RGB")
        img.thumbnail((800, 800))
        jpg = filepath.replace(ext, ".jpg")
        img.save(jpg, "JPEG", quality=70)
        os.unlink(filepath)
        return jpg
    elif ext in (".jpg", ".jpeg", ".png"):
        img = Image.open(filepath)
        img.thumbnail((800, 800))
        jpg = filepath.rsplit(".", 1)[0] + ".jpg"
        img.save(jpg, "JPEG", quality=70)
        if jpg != filepath: os.unlink(filepath)
        return jpg
    return None

def analyze_receipt(jpg_path, vendor_hint=""):
    with open(jpg_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    prompt = (
        "Extract from this receipt: amount (number only), currency (ISO 4217), date (YYYY-MM-DD), description. "
        "Report ORIGINAL amount and currency, NOT converted. "
        "Reply ONLY with JSON: {\"amount\": X, \"currency\": \"EUR\", \"date\": \"YYYY-MM-DD\", \"description\": \"...\"}"
    )
    resp = http_requests.post("https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"},
        json={"model": "openai/gpt-4o-mini", "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
        ]}], "max_tokens": 200, "temperature": 0.1}, timeout=30)
    if resp.status_code == 200:
        text = resp.json()["choices"][0]["message"]["content"].strip()
        s, e = text.find("{"), text.rfind("}") + 1
        if s >= 0 and e > s: return json.loads(text[s:e])
    else:
        print(f"    Vision error: {resp.status_code}")
    return None

def main():
    token = get_token()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT id, date, amount, vendor, description, onedrive_id, category FROM expenses WHERE (amount = 0 OR amount IS NULL) AND onedrive_id IS NOT NULL ORDER BY date")
    rows = cur.fetchall()
    print(f"Processing {len(rows)} remaining zero-amount expenses\n")

    fixed = errors = 0
    for i, row in enumerate(rows):
        eid = row["id"]
        print(f"[{i+1}/{len(rows)}] id={eid} | {row['date']} | {row['vendor']}")

        # Get file info
        try:
            info = http_requests.get(f"{GRAPH_BASE}/items/{row['onedrive_id']}?$select=name,parentReference",
                headers={"Authorization": f"Bearer {token}"}, timeout=30).json()
            fname = info.get("name", "")
            parent_id = info.get("parentReference", {}).get("id", "")
        except Exception as e:
            print(f"    ERROR: {e}")
            errors += 1; continue

        # Download
        ext = Path(fname).suffix.lower()
        tmp = tempfile.mktemp(suffix=ext)
        try:
            http_requests.get(f"{GRAPH_BASE}/items/{row['onedrive_id']}/content",
                headers={"Authorization": f"Bearer {token}"}, timeout=30)
            # Actually save the file
            r = http_requests.get(f"{GRAPH_BASE}/items/{row['onedrive_id']}/content",
                headers={"Authorization": f"Bearer {token}"}, timeout=30)
            with open(tmp, "wb") as f: f.write(r.content)
        except Exception as e:
            print(f"    ERROR downloading: {e}")
            errors += 1; continue

        # Process
        jpg = process_image(tmp)
        if not jpg:
            print(f"    ERROR converting {fname}")
            if os.path.exists(tmp): os.unlink(tmp)
            errors += 1; continue

        analysis = analyze_receipt(jpg, f"{row['vendor']} {row['description']}")
        os.unlink(jpg)
        gc.collect()

        if not analysis or not analysis.get("amount") or analysis["amount"] <= 0:
            print(f"    ERROR: could not extract amount")
            errors += 1; continue

        amount = analysis["amount"]
        currency = analysis.get("currency", "EUR").upper()
        a_date = analysis.get("date", row["date"])
        if a_date and a_date < "2025-04-01":
            a_date = row["date"]
        a_desc = analysis.get("description", row["description"])

        eur = convert_to_eur(amount, currency, a_date or row["date"])
        vat = round(eur * VAT_RATE, 2)

        new_desc = f"{a_desc} (Original: {amount:.2f} {currency})" if currency != "EUR" else a_desc
        notes = f"Original: {amount:.2f} {currency} -> {eur:.2f} EUR" if currency != "EUR" else ""

        # Rename on OneDrive
        new_onedrive_id = row["onedrive_id"]
        date_c = (a_date or row["date"]).replace("-", "")
        amt_s = f"{amount:.2f}{currency}" if currency != "EUR" else f"{eur:.2f}EUR"
        new_name = f"{date_c}_{row['vendor'].replace(' ','')}_{(row['category'] or 'other').capitalize()}_{amt_s}{ext}"
        if parent_id and fname != new_name:
            try:
                # Check if exists
                cr = http_requests.get(f"{GRAPH_BASE}/items/{parent_id}:/{new_name}",
                    headers={"Authorization": f"Bearer {token}"}, timeout=30)
                if cr.status_code == 200:
                    new_onedrive_id = cr.json()["id"]
                    http_requests.delete(f"{GRAPH_BASE}/items/{row['onedrive_id']}",
                        headers={"Authorization": f"Bearer {token}"}, timeout=30)
                else:
                    # Upload new, delete old
                    with open(tmp if os.path.exists(tmp) else "/dev/null", "rb") as f: pass
                    # Re-download for upload
                    r = http_requests.get(f"{GRAPH_BASE}/items/{row['onedrive_id']}/content",
                        headers={"Authorization": f"Bearer {token}"}, timeout=30)
                    ur = http_requests.put(f"{GRAPH_BASE}/items/{parent_id}:/{new_name}:/content",
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"},
                        data=r.content, timeout=60)
                    if ur.status_code in (200, 201):
                        new_onedrive_id = ur.json()["id"]
                        http_requests.delete(f"{GRAPH_BASE}/items/{row['onedrive_id']}",
                            headers={"Authorization": f"Bearer {token}"}, timeout=30)
                        print(f"    Renamed: {fname} -> {new_name}")
            except Exception as e:
                print(f"    Rename error (non-fatal): {e}")

        cur.execute("UPDATE expenses SET amount=?, amount_vat=?, vat_rate=?, vat_recoverable=?, description=?, notes=?, onedrive_id=?, date=? WHERE id=?",
                   (eur, vat, VAT_RATE, vat, new_desc, notes, new_onedrive_id, a_date or row["date"], eid))
        conn.commit()
        fixed += 1
        print(f"    FIXED: {eur:.2f} EUR | {new_desc[:60]}")
        time.sleep(0.8)

    conn.close()
    print(f"\nDone: {fixed} fixed, {errors} errors")

if __name__ == "__main__":
    main()

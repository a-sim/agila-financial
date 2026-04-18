#!/usr/bin/env python3
"""
One-time import: Process Tesla charging PDFs from OneDrive subfolders.
Converts PDFs to JPEG, analyzes via vision API, imports into dashboard DB.
Processes in small batches to avoid OOM on Raspberry Pi.
"""
import json, os, base64, sqlite3, subprocess, tempfile, time
from pathlib import Path
import requests as http_requests

DB_PATH = Path("/home/asimo/agila-financial-dashboard/agila.db")
TOKEN_CACHE = Path.home() / ".microsoft_mcp_token_cache.json"
GRAPH_BASE = "https://graph.microsoft.com/v1.0/me/drive"
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY") or open(Path.home() / ".openrouter-api-key").read().strip()
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Tesla subfolder IDs
TESLA_FOLDERS = [
    ("2025Q3 JUL-AUG", "017IBDTVU2XZZSHK22NZF2NID3FYIDWXRX"),
    ("2025Q3 AUG-SEP", "017IBDTVUVCH6KP2OZZRE332LK4GXBNLPU"),
    ("2025Q3 SEP-OCT", "017IBDTVUQM7Q7OI3Q35AZE64XSF6EB5NC"),
    ("2025Q4 OCT-NOV", "017IBDTVWAY7TMCK5DMFGJIOMLV2AN2SY4"),
    ("2025Q4 NOV-DEC", "017IBDTVTIJNIPSYVBCJGLJNEX2Q42I5PC"),
    ("2025Q4 DEC-JAN", "017IBDTVTAKNBJXB76DZE2DKVB2YQ7LP5D"),
]

VAT_RATE = 0.17


def get_token():
    with open(TOKEN_CACHE) as f:
        cache = json.load(f)
    return list(cache["AccessToken"].values())[0]["secret"]


def list_all_tesla_pdfs(token):
    """Recursively list all PDF files in Tesla folders."""
    headers = {"Authorization": f"Bearer {token}"}
    all_files = []
    for folder_name, folder_id in TESLA_FOLDERS:
        resp = http_requests.get(
            f"{GRAPH_BASE}/items/{folder_id}/children?$top=200",
            headers=headers
        )
        items = resp.json().get("value", [])
        for item in items:
            if item.get("folder"):
                # Recurse into UUID subfolder
                resp2 = http_requests.get(
                    f"{GRAPH_BASE}/items/{item['id']}/children",
                    headers=headers
                )
                sub_items = resp2.json().get("value", [])
                for si in sub_items:
                    if not si.get("folder") and si.get("name", "").lower().endswith(".pdf"):
                        all_files.append({
                            "folder": folder_name,
                            "name": si["name"],
                            "id": si["id"],
                            "size": si.get("size", 0),
                        })
            elif item.get("name", "").lower().endswith(".pdf"):
                all_files.append({
                    "folder": folder_name,
                    "name": item["name"],
                    "id": item["id"],
                    "size": item.get("size", 0),
                })
    return all_files


def download_and_convert(file_id, token):
    """Download a PDF from OneDrive and convert to JPEG."""
    headers = {"Authorization": f"Bearer {token}"}
    resp = http_requests.get(
        f"{GRAPH_BASE}/items/{file_id}/content",
        headers=headers
    )
    pdf_path = tempfile.mktemp(suffix=".pdf")
    with open(pdf_path, "wb") as f:
        f.write(resp.content)

    jpg_path = pdf_path.replace(".pdf", ".jpg")
    result = subprocess.run(
        ["pdftoppm", "-jpeg", "-r", "150", "-singlefile", pdf_path, jpg_path.replace(".jpg", "")],
        capture_output=True, text=True
    )
    os.unlink(pdf_path)
    if not os.path.exists(jpg_path):
        return None
    return jpg_path


def analyze_receipt(jpg_path):
    """Analyze a Tesla receipt image via vision API."""
    with open(jpg_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    prompt = (
        "You are a receipt scanner for Tesla Supercharger invoices. "
        "Extract these fields:\n"
        "1. amount: the TOTAL amount charged (number only)\n"
        "2. date: the charge date in YYYY-MM-DD format\n"
        "3. location: the charging station name/location\n"
        "4. currency: ISO code (EUR, etc.)\n\n"
        "Reply ONLY with valid JSON. Example:\n"
        '{"amount": 12.50, "date": "2025-07-15", "location": "Tesla Supercharger Arlon", "currency": "EUR"}'
    )

    resp = http_requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {OPENROUTER_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "openai/gpt-4o-mini",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
                ]
            }],
            "max_tokens": 200,
            "temperature": 0.1,
        },
        timeout=30,
    )

    if resp.status_code == 200:
        text = resp.json()["choices"][0]["message"]["content"].strip()
        start = text.find('{')
        end = text.rfind('}') + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    return None


def main():
    token = get_token()
    files = list_all_tesla_pdfs(token)
    print(f"Found {len(files)} Tesla PDF files")

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    imported = 0
    skipped = 0
    errors = 0

    for i, f in enumerate(files):
        # Check if already imported
        cur.execute("SELECT id FROM expenses WHERE onedrive_id = ?", (f["id"],))
        if cur.fetchone():
            print(f"  [{i+1}/{len(files)}] SKIP (already imported): {f['name']}")
            skipped += 1
            continue

        # Download and convert
        jpg_path = None
        try:
            jpg_path = download_and_convert(f["id"], token)
            if not jpg_path:
                print(f"  [{i+1}/{len(files)}] ERROR converting: {f['name']}")
                errors += 1
                continue

            # Analyze
            analysis = analyze_receipt(jpg_path)
        except Exception as e:
            print(f"  [{i+1}/{len(files)}] ERROR: {f['name']}: {e}")
            errors += 1
            continue
        finally:
            if jpg_path and os.path.exists(jpg_path):
                os.unlink(jpg_path)

        if not analysis:
            print(f"  [{i+1}/{len(files)}] ERROR analyzing: {f['name']}")
            errors += 1
            continue

        amount = analysis.get("amount") or 0
        date_str = analysis.get("date", "")
        location = analysis.get("location", "")
        currency = analysis.get("currency", "EUR")

        # Validate date
        if date_str and date_str < "2025-04-01":
            print(f"  [{i+1}/{len(files)}] WARNING: date {date_str} before Agila start")
            continue

        if not date_str:
            print(f"  [{i+1}/{len(files)}] SKIP: no date in {f['name']}")
            skipped += 1
            continue

        # Calculate VAT
        amount_vat = round(amount * VAT_RATE, 2)
        vat_recoverable = amount_vat

        # Build filename and description
        new_filename = f"{date_str.replace('-','')}_TeslaCharging_Car_{amount:.2f}{currency}.pdf"
        desc = f"Tesla Supercharger - {location}" if location else "Tesla Supercharger"

        # Check for duplicate
        cur.execute(
            "SELECT id FROM expenses WHERE date = ? AND vendor LIKE '%Tesla%' AND ABS(amount - ?) < 1",
            (date_str, amount)
        )
        if cur.fetchone():
            # Update existing entry with onedrive_id
            cur.execute(
                "UPDATE expenses SET onedrive_id = ?, onedrive_path = ?, description = ? WHERE date = ? AND vendor LIKE '%Tesla%' AND ABS(amount - ?) < 1",
                (f["id"], f"TeslaCharging/{f['folder']}", desc, date_str, amount)
            )
            print(f"  [{i+1}/{len(files)}] LINKED: {date_str} | EUR {amount:.2f} | {location} (updated existing)")
        else:
            # Insert new
            cur.execute("""
                INSERT INTO expenses (date, amount, amount_vat, category, vendor,
                    description, vat_rate, vat_recoverable, onedrive_id, source,
                    notes, status, onedrive_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'onedrive', ?, 'pending', ?)
            """, (
                date_str, amount, amount_vat, "car", "Tesla Charging",
                desc, VAT_RATE, vat_recoverable, f["id"],
                f"File: {new_filename} | Original: {f['name']}",
                f"TeslaCharging/{f['folder']}",
            ))
            print(f"  [{i+1}/{len(files)}] IMPORTED: {date_str} | EUR {amount:.2f} | {location}")

        imported += 1
        conn.commit()

        # Small delay to avoid rate limits
        time.sleep(1)

    conn.close()
    print(f"\nDone: {imported} processed, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    main()

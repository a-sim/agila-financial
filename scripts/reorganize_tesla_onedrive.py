#!/usr/bin/env python3
"""
One-time job: Reorganize Tesla charging PDFs on OneDrive.
- Downloads each PDF from its UUID subfolder
- Renames to standard convention: YYYYMMDD_TeslaCharging_Car_AmountEUR.pdf
- Uploads to the correct quarterly expense folder
- Deletes the old UUID subfolder

Uses expense data from the dashboard DB for the correct filenames.
"""
import json, os, sqlite3, sys, tempfile, time
from pathlib import Path
import requests as http_requests

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)

DB_PATH = Path("/home/asimo/agila-financial-dashboard/agila.db")
TOKEN_CACHE = Path.home() / ".microsoft_mcp_token_cache.json"
GRAPH_BASE = "https://graph.microsoft.com/v1.0/me/drive"

# OneDrive quarterly folder IDs
QUARTER_FOLDERS = {
    "2025Q3": "017IBDTVULQGUFAMKND5DKILTALBHECCYX",
    "2025Q4": "017IBDTVWBHE3LCF47ZJCZHF2YJUZ3AZCN",
    "2026Q1": "017IBDTVTNKFRJBZNNP5CKSZAULMNHDUML",
    "2026Q2": "017IBDTVQXONRYF6RZM5EIF4PLJJTUV4CE",
}


def get_token():
    # Refresh via mcporter first
    import subprocess
    try:
        subprocess.run(
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


def get_quarter(date_str):
    """Determine which quarter folder a date belongs to."""
    year = int(date_str[:4])
    month = int(date_str[5:7])
    q = (month - 1) // 3 + 1
    return f"{year}Q{q}"


def download_file(file_id, token, local_path):
    """Download a file from OneDrive by its item ID."""
    url = f"{GRAPH_BASE}/items/{file_id}/content"
    resp = http_requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    with open(local_path, "wb") as f:
        f.write(resp.content)
    return len(resp.content)


def upload_file(local_path, folder_id, filename, token):
    """Upload a file to a OneDrive folder."""
    url = f"{GRAPH_BASE}/items/{folder_id}:/{filename}:/content"
    with open(local_path, "rb") as f:
        content = f.read()
    resp = http_requests.put(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/pdf"},
        data=content, timeout=60
    )
    if resp.status_code in (200, 201):
        result = resp.json()
        print(f"    Uploaded: {filename} -> {result.get('id')}")
        return result.get("id")
    else:
        print(f"    ERROR uploading {filename}: {resp.status_code} {resp.text[:200]}")
        return None


def delete_item(item_id, token):
    """Delete an item from OneDrive."""
    url = f"{GRAPH_BASE}/items/{item_id}"
    resp = http_requests.delete(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    return resp.status_code == 204


def get_parent_id(item_id, token):
    """Get the parent folder ID of an item."""
    url = f"{GRAPH_BASE}/items/{item_id}?$select=parentReference"
    resp = http_requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if resp.status_code == 200:
        return resp.json().get("parentReference", {}).get("id")
    return None


def main():
    token = get_token()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Get all Tesla expenses with onedrive_ids
    cur.execute("""
        SELECT id, date, amount, vendor, description, onedrive_id, onedrive_path 
        FROM expenses 
        WHERE vendor LIKE '%Tesla%' AND onedrive_id IS NOT NULL AND source = 'onedrive'
        ORDER BY date
    """)
    rows = cur.fetchall()

    print(f"Found {len(rows)} Tesla expenses with OneDrive files to reorganize")

    moved = 0
    errors = 0

    for row in rows:
        date = row["date"]
        amount = row["amount"]
        onedrive_id = row["onedrive_id"]
        desc = row["description"] or ""

        # Build proper filename
        date_compact = date.replace("-", "")
        amount_str = f"{amount:.2f}EUR"
        # Extract location from description if available
        location = ""
        if " - " in desc:
            location = desc.split(" - ", 1)[1].replace(",", "").replace(" ", "")[:20]
        new_filename = f"{date_compact}_TeslaCharging_Car_{amount_str}.pdf"

        # Determine target quarterly folder
        quarter = get_quarter(date)
        target_folder_id = QUARTER_FOLDERS.get(quarter)
        if not target_folder_id:
            print(f"  SKIP {date}: no folder for {quarter}")
            errors += 1
            continue

        # Check if file with new name already exists in target folder
        check_url = f"{GRAPH_BASE}/items/{target_folder_id}:/{new_filename}"
        check_resp = http_requests.get(check_url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        if check_resp.status_code == 200:
            print(f"  SKIP {date}: {new_filename} already exists in {quarter}")
            # Delete old file from UUID subfolder
            delete_item(onedrive_id, token)
            # Update DB with new onedrive_id
            new_id = check_resp.json().get("id")
            cur.execute("UPDATE expenses SET onedrive_id = ?, onedrive_path = ? WHERE id = ?",
                       (new_id, f"01_Expenses_Incoming/{quarter}", row["id"]))
            conn.commit()
            moved += 1
            continue

        # Download the PDF
        tmp_path = tempfile.mktemp(suffix=".pdf")
        try:
            size = download_file(onedrive_id, token, tmp_path)
            print(f"  Downloaded: {date} | EUR {amount:.2f} | {size} bytes")
        except Exception as e:
            print(f"  ERROR downloading {onedrive_id}: {e}")
            errors += 1
            continue

        # Upload with new name to correct quarterly folder
        new_onedrive_id = upload_file(tmp_path, target_folder_id, new_filename, token)
        os.unlink(tmp_path)

        if new_onedrive_id:
            # Delete old file from UUID subfolder
            old_parent_id = get_parent_id(onedrive_id, token)
            delete_item(onedrive_id, token)

            # Try to delete the UUID parent folder if empty
            if old_parent_id:
                try:
                    time.sleep(0.5)
                    delete_item(old_parent_id, token)
                    print(f"    Deleted empty UUID folder")
                except Exception:
                    pass  # Folder might not be empty

            # Update DB
            cur.execute("UPDATE expenses SET onedrive_id = ?, onedrive_path = ? WHERE id = ?",
                       (new_onedrive_id, f"01_Expenses_Incoming/{quarter}", row["id"]))
            conn.commit()
            moved += 1
        else:
            errors += 1

        time.sleep(0.5)  # Rate limiting

    # Now try to delete remaining empty UUID subfolders in Tesla folders
    print("\nCleaning up empty UUID subfolders...")
    tesla_parent_folders = [
        ("2025Q3 JUL-AUG", "017IBDTVU2XZZSHK22NZF2NID3FYIDWXRX"),
        ("2025Q3 AUG-SEP", "017IBDTVUVCH6KP2OZZRE332LK4GXBNLPU"),
        ("2025Q3 SEP-OCT", "017IBDTVUQM7Q7OI3Q35AZE64XSF6EB5NC"),
        ("2025Q4 OCT-NOV", "017IBDTVWAY7TMCK5DMFGJIOMLV2AN2SY4"),
        ("2025Q4 NOV-DEC", "017IBDTVTIJNIPSYVBCJGLJNEX2Q42I5PC"),
        ("2025Q4 DEC-JAN", "017IBDTVTAKNBJXB76DZE2DKVB2YQ7LP5D"),
    ]

    for name, fid in tesla_parent_folders:
        resp = http_requests.get(
            f"{GRAPH_BASE}/items/{fid}/children?$top=50",
            headers={"Authorization": f"Bearer {token}"}
        )
        items = resp.json().get("value", [])
        for item in items:
            if item.get("folder"):
                child_count = item.get("folder", {}).get("childCount", 0)
                if child_count == 0:
                    if delete_item(item["id"], token):
                        print(f"  Deleted empty folder: {item['name']} in {name}")
                else:
                    print(f"  Keeping non-empty folder: {item['name']} ({child_count} items) in {name}")

    conn.close()
    print(f"\nDone: {moved} moved, {errors} errors")


if __name__ == "__main__":
    main()

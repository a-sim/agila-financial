#!/usr/bin/env python3
"""
Fix 5 (ITERATION-P2): Import missing 2025 Q2-Q4 expenses from OneDrive.

OneDrive quarterly folder IDs (2025):
  Q2: 017IBDTVVNFP7NA5ODHVH26PQTC3B3D2XF
  Q3: 017IBDTVULQGUFAMKND5DKILTALBHECCYX
  Q4: 017IBDTVWBHE3LCF47ZJCZHF2YJUZ3AZCN

Uses Microsoft Graph v1.0 with the access token from
~/.microsoft_mcp_token_cache.json (the microsoft-mcp cache).

Parses filenames using the same YYYYMMDD_Vendor_Description_AmountCUR.ext
convention as scripts/sync_onedrive.py, infers category + VAT, and inserts
rows with source='onedrive'. Skips rows whose onedrive_id already exists in
the dashboard DB.
"""
import json
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from pathlib import Path

# Reuse parsing logic from the sibling script by importing it.
sys.path.insert(0, str(Path(__file__).parent))
from sync_onedrive import (  # noqa: E402
    CATEGORY_KEYWORDS,
    NON_RECOVERABLE,
    RECEIPT_EXTENSIONS,
    SKIP_PATTERNS,
    VAT_RATES,
    infer_category,
    parse_filename,
)

DASHBOARD_DB = Path("/home/asimo/agila-financial-dashboard/agila.db")
TOKEN_CACHE = Path.home() / ".microsoft_mcp_token_cache.json"

# 2025 quarterly folder IDs
FOLDERS_2025 = {
    "2025Q2": "017IBDTVVNFP7NA5ODHVH26PQTC3B3D2XF",
    "2025Q3": "017IBDTVULQGUFAMKND5DKILTALBHECCYX",
    "2025Q4": "017IBDTVWBHE3LCF47ZJCZHF2YJUZ3AZCN",
}

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def get_token() -> str:
    with open(TOKEN_CACHE) as f:
        data = json.load(f)
    at = data["AccessToken"]
    first_key = next(iter(at))
    return at[first_key]["secret"]


def graph_get(token: str, url: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def list_folder_files(token: str, folder_id: str) -> list:
    """List all file children of a drive item (folder) by id, handling paging."""
    url = (
        f"{GRAPH_BASE}/me/drive/items/{folder_id}/children"
        "?$select=id,name,size,file,folder,parentReference&$top=200"
    )
    items = []
    while url:
        data = graph_get(token, url)
        for it in data.get("value", []):
            if it.get("file"):
                items.append(it)
        url = data.get("@odata.nextLink")
    return items


def import_folder(cur, token: str, label: str, folder_id: str):
    print(f"\n--- {label} ({folder_id}) ---")
    files = list_folder_files(token, folder_id)
    print(f"  {len(files)} files listed")

    imported = 0
    skipped_nonreceipt = 0
    skipped_existing = 0
    skipped_parse = 0
    for f in files:
        name = f["name"]
        parsed = parse_filename(name)
        if not parsed:
            skipped_nonreceipt += 1
            continue

        cur.execute("SELECT id FROM expenses WHERE onedrive_id = ?", (f["id"],))
        if cur.fetchone():
            skipped_existing += 1
            continue

        amount = parsed["amount"] or 0.0
        category = infer_category(parsed["vendor"], parsed["description"])
        vat_rate = VAT_RATES.get(category, 0.17)
        if amount > 0:
            amount_vat = round(amount * vat_rate, 2)
            vat_recoverable = 0.0 if category in NON_RECOVERABLE else amount_vat
        else:
            amount_vat = 0.0
            vat_recoverable = 0.0

        notes = f"File: {name} | Path: {label}"
        try:
            cur.execute(
                """INSERT INTO expenses
                   (date, amount, amount_vat, category, vendor, description,
                    vat_rate, vat_recoverable, onedrive_id, source, notes, status)
                   VALUES (?,?,?,?,?,?,?,?,?, 'onedrive', ?, 'pending')""",
                (
                    parsed["date"],
                    amount,
                    amount_vat,
                    category,
                    parsed["vendor"],
                    parsed["description"] or parsed["vendor_desc"],
                    vat_rate,
                    vat_recoverable,
                    f["id"],
                    notes,
                ),
            )
            imported += 1
            print(f"  IMPORT {parsed['date']} | EUR {amount:>8.2f} | "
                  f"{category:12s} | {parsed['vendor']} | {name}")
        except Exception as e:
            print(f"  ERROR inserting {name}: {e}")
            skipped_parse += 1

    print(f"  imported={imported} existing={skipped_existing} "
          f"non-receipt={skipped_nonreceipt} parse-errors={skipped_parse}")
    return imported


def main():
    token = get_token()
    conn = sqlite3.connect(str(DASHBOARD_DB))
    cur = conn.cursor()

    total = 0
    for label, folder_id in FOLDERS_2025.items():
        total += import_folder(cur, token, label, folder_id)

    cur.execute(
        "INSERT INTO sync_log (source, action, records_affected, status) "
        "VALUES ('fix_script','import_2025_expenses',?,?)",
        (total, "ok"),
    )
    conn.commit()
    conn.close()
    print(f"\nTotal imported: {total}")


if __name__ == "__main__":
    main()

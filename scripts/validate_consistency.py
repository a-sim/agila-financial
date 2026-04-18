#!/usr/bin/env python3
"""
Fix 6 (ITERATION-P2): Cross-application consistency validator.

Reports (does NOT auto-fix) inconsistencies between:
  - Dashboard DB  (/home/asimo/agila-financial-dashboard/agila.db)
  - Receipt bot DB (/home/asimo/.agila-telegram/receipts.db)
  - OneDrive (2025 Q2-Q4 + 2026 Q1-Q2 receipt folders)

Checks performed:
  1. Every OneDrive receipt file has a matching expense in dashboard
     (matched via onedrive_id).
  2. Every bot-DB receipt has a matching dashboard expense (matched via
     receipt_id = uuid).
  3. Dashboard rows with source='telegram' but missing receipt_id or missing
     corresponding bot receipt.
  4. Date mismatches: dashboard.date vs bot.date_str (telegram rows).
  5. Date mismatches: dashboard.date vs YYYYMMDD filename prefix (onedrive rows).
  6. Amount mismatches: dashboard.amount vs bot.amount (telegram rows, EUR).
  7. Duplicate dashboard rows (same receipt_id OR same onedrive_id appearing
     more than once).
"""
import json
import re
import sqlite3
import urllib.request
from pathlib import Path

DASHBOARD_DB = Path("/home/asimo/agila-financial-dashboard/agila.db")
BOT_DB = Path("/home/asimo/.agila-telegram/receipts.db")
TOKEN_CACHE = Path.home() / ".microsoft_mcp_token_cache.json"

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Folder IDs we scan on OneDrive for receipts
ONEDRIVE_FOLDERS = {
    "2025Q2": "017IBDTVVNFP7NA5ODHVH26PQTC3B3D2XF",
    "2025Q3": "017IBDTVULQGUFAMKND5DKILTALBHECCYX",
    "2025Q4": "017IBDTVWBHE3LCF47ZJCZHF2YJUZ3AZCN",
    # 2026 Q1-Q2 were added to SCAN_PATHS in sync_onedrive.py by path. We
    # resolve the IDs via Graph by path lookup below.
}

# 2026 quarterly folder paths (by path so we don't hard-code more IDs)
ONEDRIVE_PATHS_2026 = [
    ("2026Q1",
     "01_Agila_Lux/01_Accounting/01_Invoices-Expenses_Agila_SHARED/"
     "01_Expenses_Incoming/2026/2026_Invoices-Receipts-Statements/"
     "2026Q1_Invoices-Receipts"),
    ("2026Q2",
     "01_Agila_Lux/01_Accounting/01_Invoices-Expenses_Agila_SHARED/"
     "01_Expenses_Incoming/2026/2026_Invoices-Receipts-Statements/"
     "2026Q2_Invoices-Receipts"),
]

SKIP_NAME_PATTERNS = [
    "Bank_Statement", "Statement", "Salary", "PaySlip",
    "VAT_Declaration", "TVA", "Reminders", "Parking_Reminder",
]
RECEIPT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".pdf", ".xml"}

AMOUNT_TOLERANCE_EUR = 0.02


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
    url = (
        f"{GRAPH_BASE}/me/drive/items/{folder_id}/children"
        "?$select=id,name,file,folder&$top=200"
    )
    items = []
    while url:
        data = graph_get(token, url)
        for it in data.get("value", []):
            if it.get("file"):
                items.append(it)
        url = data.get("@odata.nextLink")
    return items


def resolve_path_to_id(token: str, path: str) -> str:
    url = f"{GRAPH_BASE}/me/drive/root:/{path}"
    data = graph_get(token, url)
    return data["id"]


def is_receipt_name(name: str) -> bool:
    lname = name.lower()
    if any(p.lower() in lname for p in SKIP_NAME_PATTERNS):
        return False
    dot = name.rfind(".")
    ext = name[dot:].lower() if dot >= 0 else ""
    return ext in RECEIPT_EXTENSIONS


def filename_date(name: str):
    m = re.match(r"^(\d{8})_", name)
    if not m:
        return None
    ymd = m.group(1)
    try:
        return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
    except Exception:
        return None


def main():
    token = get_token()

    # Gather all OneDrive files
    print("Scanning OneDrive folders...")
    drive_files = {}  # onedrive_id -> {name, label}
    for label, fid in ONEDRIVE_FOLDERS.items():
        files = list_folder_files(token, fid)
        for f in files:
            drive_files[f["id"]] = {"name": f["name"], "label": label}
        print(f"  {label}: {len(files)} files")
    for label, path in ONEDRIVE_PATHS_2026:
        try:
            fid = resolve_path_to_id(token, path)
        except Exception as e:
            print(f"  {label}: could not resolve path ({e})")
            continue
        files = list_folder_files(token, fid)
        for f in files:
            drive_files[f["id"]] = {"name": f["name"], "label": label}
        print(f"  {label}: {len(files)} files")
    print(f"Total OneDrive items: {len(drive_files)}")

    # Load dashboard and bot DB rows
    dash = sqlite3.connect(str(DASHBOARD_DB))
    dash.row_factory = sqlite3.Row
    bot = sqlite3.connect(str(BOT_DB))
    bot.row_factory = sqlite3.Row

    dash_rows = [dict(r) for r in dash.execute(
        "SELECT id, date, vendor, amount, source, receipt_id, onedrive_id, notes "
        "FROM expenses").fetchall()]
    bot_rows = [dict(r) for r in bot.execute(
        "SELECT uuid, date_str, vendor, amount, amount_original, currency, filename "
        "FROM receipts").fetchall()]

    dash_by_onedrive = {r["onedrive_id"]: r for r in dash_rows if r["onedrive_id"]}
    dash_by_receipt = {}
    for r in dash_rows:
        if r["receipt_id"]:
            dash_by_receipt.setdefault(r["receipt_id"], []).append(r)

    issues = {
        "missing_dashboard_for_onedrive": [],
        "missing_dashboard_for_bot": [],
        "telegram_missing_receipt_id": [],
        "telegram_orphan_receipt_id": [],
        "date_mismatch_telegram": [],
        "date_mismatch_onedrive": [],
        "amount_mismatch_telegram": [],
        "duplicate_receipt_id": [],
        "duplicate_onedrive_id": [],
        "onedrive_non_receipt_unskipped": [],
    }

    # 1. OneDrive files -> dashboard
    for oid, meta in drive_files.items():
        if not is_receipt_name(meta["name"]):
            continue
        if oid not in dash_by_onedrive:
            issues["missing_dashboard_for_onedrive"].append(
                f"{meta['label']} | {meta['name']} (id={oid})"
            )

    # 2. Bot receipts -> dashboard
    for b in bot_rows:
        uid = b["uuid"]
        if not uid:
            continue
        if uid not in dash_by_receipt:
            issues["missing_dashboard_for_bot"].append(
                f"bot uuid={uid} {b['date_str']} {b['vendor']} {b['amount']}"
            )

    # 3. Telegram rows missing receipt_id or with orphan receipt_id
    bot_uuids = {b["uuid"] for b in bot_rows if b["uuid"]}
    for r in dash_rows:
        if r["source"] == "telegram":
            if not r["receipt_id"]:
                issues["telegram_missing_receipt_id"].append(
                    f"dash id={r['id']} {r['date']} {r['vendor']} {r['amount']}"
                )
            elif r["receipt_id"] not in bot_uuids:
                issues["telegram_orphan_receipt_id"].append(
                    f"dash id={r['id']} receipt_id={r['receipt_id']} "
                    f"{r['vendor']}"
                )

    # 4. Date mismatches for telegram rows
    bot_by_uuid = {b["uuid"]: b for b in bot_rows if b["uuid"]}
    for r in dash_rows:
        if r["source"] != "telegram" or not r["receipt_id"]:
            continue
        b = bot_by_uuid.get(r["receipt_id"])
        if not b or not b["date_str"]:
            continue
        if r["date"] != b["date_str"]:
            issues["date_mismatch_telegram"].append(
                f"dash id={r['id']} date={r['date']} vs bot "
                f"{b['date_str']} ({r['vendor']})"
            )

    # 5. Date mismatches for onedrive rows
    for r in dash_rows:
        if r["source"] != "onedrive" or not r["notes"]:
            continue
        m = re.search(r"File:\s*(\d{8})_", r["notes"])
        if not m:
            continue
        ymd = m.group(1)
        iso = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        if r["date"] != iso:
            issues["date_mismatch_onedrive"].append(
                f"dash id={r['id']} date={r['date']} vs filename "
                f"{iso} ({r['vendor']})"
            )

    # 6. Amount mismatches telegram
    for r in dash_rows:
        if r["source"] != "telegram" or not r["receipt_id"]:
            continue
        b = bot_by_uuid.get(r["receipt_id"])
        if not b or b["amount"] is None:
            continue
        if abs((r["amount"] or 0) - (b["amount"] or 0)) > AMOUNT_TOLERANCE_EUR:
            issues["amount_mismatch_telegram"].append(
                f"dash id={r['id']} {r['vendor']} amount={r['amount']} "
                f"vs bot {b['amount']} EUR"
            )

    # 7. Duplicates
    counts_r, counts_o = {}, {}
    for r in dash_rows:
        if r["receipt_id"]:
            counts_r[r["receipt_id"]] = counts_r.get(r["receipt_id"], 0) + 1
        if r["onedrive_id"]:
            counts_o[r["onedrive_id"]] = counts_o.get(r["onedrive_id"], 0) + 1
    for rid, c in counts_r.items():
        if c > 1:
            issues["duplicate_receipt_id"].append(f"receipt_id={rid} x{c}")
    for oid, c in counts_o.items():
        if c > 1:
            issues["duplicate_onedrive_id"].append(f"onedrive_id={oid} x{c}")

    # Report
    print("\n" + "=" * 60)
    print("CONSISTENCY REPORT")
    print("=" * 60)
    for key, entries in issues.items():
        print(f"\n[{len(entries)}] {key}")
        for e in entries[:25]:
            print(f"    - {e}")
        if len(entries) > 25:
            print(f"    ... and {len(entries) - 25} more")

    dash.close()
    bot.close()


if __name__ == "__main__":
    main()

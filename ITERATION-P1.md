# Iteration P1: Reconciliation Unification + Expense UX

## Context
The Agila Financial Dashboard has two separate reconciliation data sources that are not unified in the UI:
- `reconciliation_data` table: Everest card spreadsheet import (Q4 2025, Q1 2026) — 116 rows
- `bank_transactions` table: Odoo-synced Revolut data (Dec 2025 - Apr 2026) — 120 rows

The Reconciliation tab (`/api/reconciliation`) only reads from `reconciliation_data`, making it blind to all Revolut/bank_transactions data.

Additionally, the Expenses tab has poor navigation: 100-row API limit, 50-row UI limit, no detail view on click, and a dead `status` column.

## Tasks

### T3: Unify reconciliation view

**Backend changes** (`backend/routers/reconciliation.py`):
- Modify `GET /api/reconciliation` to return transactions from BOTH `reconciliation_data` AND `bank_transactions` tables
- Normalize the schema: both tables should map to a common format:
  ```json
  {
    "id": "bt-123" or "rd-45",
    "date": "2026-04-10",
    "bank": "Everest" or "Revolut",
    "description": "...",
    "amount": -50.00,
    "currency": "EUR",
    "match_status": "MATCHED" | "MISSING - HIGH" | etc,
    "receipt_file": "...",
    "notes": "...",
    "period": "Q2 2026",
    "source_table": "reconciliation_data" | "bank_transactions"
  }
  ```
- For `bank_transactions`: compute `period` from date (Q1/Q2/Q3/Q4 + year). Map `reconciliation_status` to `match_status`:
  - `matched` → `MATCHED`
  - `pending_review` → `PENDING`
  - `transfer` → `NO RECEIPT NEEDED`
  - `unmatched` → `MISSING - MEDIUM`
- Summary stats should count both tables
- `by_period` should include all quarters found in either table
- Preserve the existing filter parameters (period, match_status, bank, date_from, date_to, amount_min, amount_max)

**Frontend changes** (`index.html`):
- The `loadBank()` function already works well with the unified format — minimal changes needed
- Ensure period dropdown includes Q2 2026 and Q3 2026 (compute from data, don't hardcode)
- Add bank filter option for "Odoo" or "Revolut" alongside "Everest" and "Revolut"

### T4: Add Q2/Q3 to reconciliation

**Backend/Script changes**:
- Update `scripts/sync_onedrive.py` SCAN_PATHS to include Q3 2026 folder:
  `01_Agila_Lux/01_Accounting/01_Invoices-Expenses_Agila_SHARED/01_Expenses_Incoming/2026/2026_Invoices-Receipts-Statements/2026Q3_Invoices-Receipts`
  (add it conditionally — only if the folder exists on OneDrive)
- The `reconciliation_data` table only has Q4 2025 and Q1 2026 from a manual spreadsheet import. Since we're now unifying with `bank_transactions`, we don't need to backfill `reconciliation_data` for Q2/Q3. The unified view will automatically pick up Q2/Q3 from `bank_transactions`.
- Verify that Q2/Q3 bank transactions exist in `bank_transactions` (they do: Q2 has 15 rows, Q3 not yet but will come as data arrives)

### T5: Full expense browse & search

**Backend changes** (`backend/routers/expenses.py`):
- Remove the 100-row limit from the expenses API (or make it configurable with a `limit` query param, default 500)
- Add query parameters: `vendor`, `category`, `source`, `date_from`, `date_to`, `search` (free text search across vendor, notes, description, email_subject)
- Return total count alongside expenses for pagination

**Frontend changes** (`index.html`):
- Remove the `.slice(0, 50)` limit in `loadExpenses()`
- Add a search bar at the top of the expenses tab (already has `expSearch` input but it only filters the visible table rows)
- Add category filter buttons (pill-style, like the bank tab filters)
- Add a quarter/period selector for the expenses tab
- Show total count: "Showing 245 expenses (EUR 23,397)"

### T6: Expense detail panel

**Frontend changes** (`index.html`):
- Make `toggleExpDetail(el)` actually work
- When clicking an expense row, expand a detail panel below it showing:
  - Vendor
  - Full amount breakdown: Net EUR X + VAT EUR Y (Z% rate) = EUR Total
  - VAT recovery: EUR X recoverable, EUR Y non-recoverable
  - Source: onedrive / telegram / bank_match / email / manual
  - OneDrive path (if available)
  - Email subject (if available from email source)
  - Notes
  - Receipt ID / link
  - Linked bank transaction (if matched)
- Style it like the bank transaction detail panel (tx-detail class)

### T7: Expense status workflow

**Backend changes** (`backend/routers/expenses.py`):
- Add `PUT /api/expenses/{id}/status` endpoint accepting `{status: "confirmed"}` or `{status: "pending"}`
- Add a `confirmed` column display in the UI

**Frontend changes** (`index.html`):
- In the expense detail panel (T6), add a "Confirm" / "Unconfirm" toggle button
- Show a small checkmark icon (✓) next to confirmed expenses in the table
- Default all existing expenses to `pending` (already the case)

## Technical Notes

- **DB path**: `/home/asimo/agila-financial-dashboard/agila.db`
- **Backend**: FastAPI at `backend/main.py` and `backend/routers/`
- **Frontend**: Single `index.html` at project root (also served at `/`)
- **Service**: `agila-financial.service` on port 8081
- **Restart after changes**: `sudo systemctl restart agila-financial.service`
- **Test after each change**: `curl -s http://localhost:8081/api/summary | python3 -m json.tool`
- **DB schema reference**: See `database.py` for table definitions
- **Do NOT modify the database schema** (no ALTER TABLE) unless absolutely necessary — the existing columns are sufficient
- **CSS**: All styles are inline in index.html, dark theme with CSS variables
- **Charts**: Chart.js via CDN

## Verification Steps
After completing all tasks:
1. `curl -s http://localhost:8081/api/reconciliation?period=Q2%202026 | python3 -m json.tool` — should show Q2 data from both tables
2. `curl -s http://localhost:8081/api/expenses?limit=500 | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('expenses',[])))"` — should return >100
3. Open dashboard in browser, click Reconciliation tab, verify Q2 2026 appears in period dropdown
4. Click an expense row, verify detail panel expands with full breakdown
5. Click "Confirm" on an expense, verify status changes

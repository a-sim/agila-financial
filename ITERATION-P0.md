# ITERATION P0 — Fix Core Gaps: Schema, Odoo Sync, VAT Logic, Reconciliation Engine

## Context

The Agila Financial Dashboard is running on port 8081 (systemd service active) but has critical gaps between the SPEC and implementation. This iteration fixes the P0 issues that make the dashboard unreliable for real financial data.

## Current State

- FastAPI backend on port 8081, SQLite at `/home/asimo/agila-financial-dashboard/agila.db`
- 4 invoices (seed data, 3 marked `paid` but INV/2026/00003 has €12,968.75 outstanding in Odoo)
- 18 expenses (seed data, fictional)
- 111 bank_transactions (synced from Odoo but minimal data: no partner/description, just `small_expense` category)
- 116 reconciliation_data rows (manually seeded from xlsx, no auto-matching)
- 0 companies
- Odoo API works: `https://agila-consulting-sarl.odoo.com/jsonrpc`, DB=`agila-consulting-sarl`, UID=2, API key in `backend/services/odoo_service.py`
- Telegram bot receipts DB at `~/.agila-telegram/receipts.db` (0 receipts currently)

## Changes Required

### 1. Database Schema Migration

Add missing columns to align with SPEC. Use ALTER TABLE (SQLite compatible) to add columns to existing tables without dropping data. Create a migration script `scripts/migrate_v2.py`.

**invoices table — add columns:**
- `type TEXT DEFAULT 'outbound'` — 'outbound' or 'inbound'
- `due_date TEXT`
- `vat_rate REAL DEFAULT 0` — 0 for reverse charge, 17 for standard
- `vat_recoverable REAL DEFAULT 0`
- `client_supplier_id INTEGER`
- `client_supplier_name TEXT`
- `odoo_link TEXT`

**expenses table — add columns:**
- `amount_vat REAL DEFAULT 0` — actual VAT amount on the expense
- `vat_rate REAL DEFAULT 0` — VAT rate applied (0%, 17%, 3%)
- `vat_recoverable REAL DEFAULT 0` — amount of VAT that can be deducted (not boolean)
- `matched_to_bank_id INTEGER`
- `receipt_id TEXT` — link to Telegram bot UUID
- `description TEXT`

**bank_transactions table — add columns:**
- `vendor_inferred TEXT`
- `journal_name TEXT`
- `matched_to_expense_id INTEGER`
- `matched_to_invoice_id INTEGER`
- `match_confidence REAL DEFAULT 0`
- `reconciliation_status TEXT DEFAULT 'unmatched'` — unmatched/pending/matched/reviewed
- `currency TEXT DEFAULT 'EUR'`

**Drop the old boolean `vat_recoverable` from expenses and replace with the new REAL column.** Since SQLite doesn't support DROP COLUMN well, the migration should:
1. Create new table with full schema
2. Copy data from old table (convert boolean vat_recoverable=1 → vat_recoverable=amount*0.17, vat_recoverable=0 → 0)
3. Drop old table
4. Rename new table

### 2. Odoo Live Sync Service

Create `scripts/sync_odoo.py` that:

a) **Sync invoices** — Pull all posted out_invoice records from Odoo, upsert into `invoices` table:
   - Map Odoo fields: name → number, invoice_date → date, invoice_date_due → due_date, amount_total → amount_gross, amount_residual → use to determine status (residual=0 → 'paid', residual>0 → 'posted'), partner_id → client_supplier_name
   - Set vat_rate=0, vat_amount=0 for EU B2B reverse charge
   - Set type='outbound'
   - Store odoo_id
   - Use `INSERT OR REPLACE` by odoo_id

b) **Sync bank entries** — Pull journal entries (move_type='entry', journal_id for bank) from Odoo:
   - Get the actual journal entries with line items
   - Try Odoo endpoint: `account.bank.statement.line` for richer bank data (has payment_ref, partner_id)
   - If bank.statement.line doesn't work, fall back to `account.move.line` filtered by journal
   - Store with proper description, partner, amount, date
   - Set reconciliation_status='unmatched' by default

c) **Log sync operations** in `sync_log` table

### 3. Fix VAT Calculation Logic

Update `backend/routers/vat.py` and `backend/routers/expenses.py` to use per-category VAT rates:

**Category → VAT rate mapping:**
```python
VAT_RATES = {
    'restaurant': 0.17,     # VAT charged but NOT deductible (recovery=0%)
    'hotel': 0.03,          # Super-reduced rate for accommodation, partially deductible
    'travel': 0.17,         # Standard rate, fully deductible
    'flight': 0.17,         # Standard rate, fully deductible
    'taxi': 0.17,           # Standard rate, fully deductible
    'software': 0.17,       # Standard rate, fully deductible
    'subscription': 0.17,   # Standard rate, fully deductible
    'office': 0.17,         # Standard rate, fully deductible
    'supplies': 0.17,       # Standard rate, fully deductible
    'professional': 0.17,   # Standard rate, fully deductible
    'professional_services': 0.17,  # Standard rate, fully deductible
    'other': 0.17,          # Standard rate, fully deductible
}

# NON-RECOVERABLE categories (VAT is charged but you cannot deduct it)
NON_RECOVERABLE = {'restaurant'}
```

Update the VAT summary endpoint to:
- Calculate input VAT correctly per category
- Show recoverable vs non-recoverable breakdown clearly
- Restaurant expenses: VAT is 17% on the gross, but 0% is recoverable
- All other categories: 17% (or 3% for hotel) is fully recoverable

### 4. Reconciliation Engine

Create `backend/services/reconciliation.py` with matching logic:

```python
def run_reconciliation():
    """
    Match bank transactions to invoices and expenses.
    """
    # Step 1: Load unmatched bank transactions
    # Step 2: Load invoices (outbound, status='posted' with residual > 0)
    # Step 3: Load expenses (not yet matched)
    
    # For each bank transaction:
    #   a) If amount is positive (inflow):
    #      - Match against outstanding invoices (same amount ±€0.01, date within 30 days of invoice)
    #      - Mark as matched with confidence score
    #   b) If amount is negative (outflow):
    #      - Match against expenses (same amount ±€0.01, date within 5 days)
    #      - Vendor name fuzzy match → boost confidence
    #   c) No match → keep as unmatched
    
    # Update match fields on bank_transactions
    # Update matched_to_bank_id on expenses/invoices
    # Return summary: matched count, unmatched count, low-confidence count
```

Add API endpoint: `POST /api/reconcile` that triggers the engine and returns results.

### 5. Telegram Bot → Expenses Sync

Create `scripts/sync_telegram.py` that:
- Reads receipts from `~/.agila-telegram/receipts.db`
- For each receipt not yet in the expenses table (check by receipt_id = uuid):
  - Insert into expenses with proper category, vendor, amount, date
  - Calculate vat_rate and vat_recoverable based on category
  - Set source='telegram'
- Run after Odoo sync in the same cron job

### 6. Update Dashboard Index

Fix the following in `index.html`:
- Revenue chart: use monthly_totals from the API correctly (it's a dict, not a list)
- Expenses: show proper category breakdown with VAT recovery per category
- VAT view: show the corrected per-category breakdown with recoverable/non-recoverable
- Add a "Sync Now" button in the Overview tab that calls `POST /api/sync/odoo`
- Show INV/2026/00003 as "outstanding" (€12,968.75) instead of "paid"

### 7. Sync API Endpoints

Add to `backend/routers/reconciliation.py` or create new router:
- `POST /api/sync/odoo` — trigger Odoo sync (invoices + bank entries)
- `POST /api/sync/telegram` — trigger Telegram receipts sync
- `POST /api/reconcile` — trigger reconciliation engine
- `GET /api/sync/status` — last sync timestamps from sync_log

### 8. Update Summary Endpoint

Fix `GET /api/summary` to:
- Use correct invoice statuses from Odoo (don't hardcode)
- Calculate outstanding from `amount_residual > 0` invoices (add `amount_residual` column to invoices if needed)
- Show April 2026 revenue based on actual data
- Use real reconciliation stats

## File Structure After

```
agila-financial-dashboard/
├── agila.db                    # Migrated to v2 schema
├── agila-financial.service     # unchanged
├── database.py                 # Updated with v2 schema + migration
├── models.py                   # Updated with new fields
├── SPEC.md                     # unchanged
├── ROADMAP.md                  # Updated with P0 completion status
├── ITERATION-P0.md             # This file
├── backend/
│   ├── main.py                 # Add sync endpoints
│   ├── routers/
│   │   ├── revenue.py          # Updated for new schema
│   │   ├── expenses.py         # Updated VAT logic
│   │   ├── vat.py              # Corrected per-category VAT
│   │   ├── bank.py             # Updated for new schema
│   │   ├── documents.py        # Updated for Telegram sync
│   │   └── reconciliation.py   # Add reconcile + sync endpoints
│   └── services/
│       ├── odoo_service.py     # Enhanced with bank statement sync
│       ├── onedrive_service.py # unchanged (still stub)
│       ├── revolut_service.py  # unchanged (still stub)
│       └── reconciliation.py   # NEW: matching engine
├── scripts/
│   ├── seed_data.py            # unchanged
│   ├── migrate_v2.py           # NEW: schema migration
│   ├── sync_odoo.py            # NEW: Odoo data sync
│   └── sync_telegram.py        # NEW: Telegram receipts sync
├── data/                       # unchanged
├── static/                     # unchanged
└── index.html                  # Updated dashboard
```

## Testing

After all changes:
1. Run `python3 scripts/migrate_v2.py` to migrate the DB
2. Run `python3 scripts/sync_odoo.py` to pull live Odoo data
3. Run `python3 scripts/sync_telegram.py` to sync any receipts
4. Restart the service: `sudo systemctl restart agila-financial`
5. Test each API endpoint with curl
6. Verify dashboard loads and shows correct data
7. Verify INV/2026/00003 shows as outstanding (€12,968.75)
8. Verify VAT calculation separates restaurant (0% recovery) from software (17% recovery)

## Constraints

- Python 3 on Raspberry Pi 5 (ARM64)
- No new pip packages — use only stdlib + what's already installed (fastapi, uvicorn, pydantic, sqlite3)
- Keep the service running on port 8081
- Don't break existing reconciliation_data (the manually seeded xlsx data)
- Odoo API key is already in `odoo_service.py` — use it directly
- The systemd service runs as user `asimo`

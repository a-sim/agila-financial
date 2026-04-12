# Agila Financial Dashboard — SPEC.md

## Context

Alejandro Simó runs Agila Consulting SARL (Luxembourg). He invoices Mayker NV €625/day.
Accountants: ATTC Group (ATTC.lu). Bank: Revolut Business. Receipts via Telegram bot.

**The core problem:** Alejandro has data scattered across Odoo (invoices), Revolut (bank transactions),
OneDrive (PDFs, CSVs), and email. Nothing talks to anything. He wants:
1. Revenue tracking (invoiced vs paid)
2. Expense tracking (with VAT recovery analysis)
3. Bank reconciliation (automatic matching of transactions to invoices/receipts)
4. VAT position (quarterly)
5. Telegram bot for receipt capture

## Data Sources (IN USE)

### 1. Odoo (https://agila-consulting-sarl.odoo.com)
- **Outbound invoices** (Agila → Mayker): `account.move` with `move_type=out_invoice`
- **Journal entries**: `account.move` with `move_type=entry` — these ARE the Revolut bank transactions synced into Odoo
- **Auth**: uid=2 (public automation user) + API key as password
- All EU B2B invoices are 0% VAT (reverse charge)
- Odoo does NOT store merchant descriptions for bank entries — use OneDrive statements instead

### 2. OneDrive (Microsoft Graph API)
- **Token**: `~/.microsoft_mcp_token_cache.json` (microsoft-mcp cache)
- **Account ID**: `87fbfc0e-dfa2-4621-aab2-319dad4e93ae.c44c0a70-24ac-4b5c-adc5-8c24d4f62e21`
- **Key folders**:
  - `01_Agila_Lux/01_Accounting/01_Invoices-Expenses_Agila_SHARED/01_Expenses_Incoming/2026/` — incoming receipts/statements
  - `01_Agila_Lux/01_Accounting/01_Invoices-Expenses_Agila_SHARED/` — contains `Receipt_Matchmaking_Q42025_Q12026.xlsx`
  - `01_Agila_Lux/01_Accounting/04_Banking/Revolut/2026/` — Revolut CSV/PDF statements
  - `01_Agila_Lux/01_Accounting/04_Receipts-Agila/2026/` — Telegram bot uploads

### 3. Agila Email (Microsoft Graph)
- Inbox: `account_id=87fbfc0e-dfa2-4621-aab2-319dad4e93ae.c44c0a70-24ac-4b5c-adc5-8c24d4f62e21`
- Folder structure: `Agila/LU/LU_Int/Accounting/` — ATTC correspondence, VAT filings
- Folder structure: `Agila/ES/ES_Proj/IVO_*` — family business invoices (not Agila)

### 4. Telegram Bot (`@agila_receipts_bot`)
- Running on Pi at `/home/asimo/.agila-telegram/receipt_bot.py`
- SQLite DB: `/home/asimo/.agila-telegram/receipts.db`
- Upload flow: photo → caption parser → OneDrive upload → metadata in DB
- Folder: `01_Agila_Lux/01_Accounting/04_Receipts-Agila/YYYY/MM-MonthName/`

## Architecture

```
[DATA LAYER]
┌─────────────────────────────────────────────────────────────┐
│  Odoo API          OneDrive/Email     Telegram Bot          │
│  - Outbound inv    - Bank statements   - Receipt photos     │
│  - Bank entries    - PDFs              - Receipt DB          │
│  - Journal lines  - Matchmaking xlsx  - Vendor/category     │
└──────────────┬──────────────┬──────────────┬────────────────┘
               │              │              │
               ▼              ▼              ▼
         [RECONCILIATION ENGINE]
         Matches bank txns ↔ invoices/receipts
         Rules:
           - Same amount (±€0.01) + date within 5 days → MATCH
           - Same vendor name substring → MATCH
           - Unmatched → flagged for review
               │
               ▼
         [SQLite: agila.db]
         Tables:
           - companies: id, name
           - invoices: id, odoo_id, number, date, due_date, amount_gross, amount_net, client, status
           - expenses: id, date, amount, category, vendor, notes, vat_recoverable, source, onedrive_path
           - bank_txns: id, odoo_id, date, amount, description, matched_to, match_confidence
           - vat_returns: id, quarter, year, output_vat, input_vat, net_vat, status, due_date
               │
               ▼
         [FastAPI: port 8081]
         /api/summary
         /api/revenue
         /api/expenses
         /api/vat
         /api/bank  ← reconciliation view
         /api/documents
         /api/reconcile  ← trigger reconciliation
         /api/chat  ← Q&A over financial data
               │
               ▼
         [HTML Dashboard: index.html]
         Mobile-first, Chart.js, dark professional theme
```

## VAT Logic (CORRECTED)

**Key rule:** Restaurant/meals = VAT NOT deductible. Software/services = VAT deductible (17%).

### Output VAT (VAT collected)
- EU B2B reverse charge: 0% on invoice, client self-accounts
- Domestic services (if any): 17%

### Input VAT (VAT paid on purchases)
- Software/subscriptions: 17% recoverable
- Professional services (ATTC): 17% recoverable
- Office supplies: 17% recoverable
- Restaurant/meals: 0% NOT recoverable
- Travel (train, flight): 17% recoverable
- Hotel: partial recovery (3% super-reduced for accommodation)

### Net VAT = Output VAT - Input VAT

## Reconciliation Engine

```
Step 1: Load bank transactions
  - Source: Odoo `account.move` entries (move_type=entry, journal_id=13 Bank)
  - Enrich with: merchant name from OneDrive CSV/PDF if available
  - Result: list of (date, amount, description, source)

Step 2: Load invoices (inbound + outbound)
  - Outbound: Odoo `account.move` (move_type=out_invoice, state=posted)
  - Inbound: from expenses table (receipts with vendor invoices)

Step 3: Match
  For each bank transaction:
    a) Exact amount match within ±5 days → high confidence MATCH
    b) Vendor name match → medium confidence
    c) Date range + amount range → low confidence
    d) No match → UNMATCHED (flag for review)

Step 4: Output
  - MATCHED: green, linked to source document
  - UNMATCHED: red, needs manual review
  - DUPLICATE: yellow, potential double payment
```

## Database Schema

```sql
-- Companies (clients/suppliers)
CREATE TABLE companies (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT CHECK(type IN ('client','supplier','both')),
    vat_number TEXT,
    country TEXT,
    notes TEXT
);

-- Invoices issued by Agila
CREATE TABLE invoices (
    id INTEGER PRIMARY KEY,
    odoo_id INTEGER UNIQUE,
    number TEXT UNIQUE NOT NULL,
    type TEXT CHECK(type IN ('outbound','inbound')),
    date TEXT NOT NULL,
    due_date TEXT,
    amount_gross REAL NOT NULL,
    amount_net REAL NOT NULL,
    vat_amount REAL DEFAULT 0,
    vat_rate REAL DEFAULT 0,  -- 0 for reverse charge, 17 for standard
    vat_recoverable REAL DEFAULT 0,
    client_supplier_id INTEGER REFERENCES companies(id),
    client_supplier_name TEXT,
    status TEXT CHECK(status IN ('draft','posted','paid','cancelled','overdue')),
    odoo_link TEXT,
    notes TEXT
);

-- Expenses (receipts, purchase invoices)
CREATE TABLE expenses (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    amount REAL NOT NULL,
    amount_vat REAL DEFAULT 0,
    vat_rate REAL DEFAULT 0,
    vat_recoverable REAL DEFAULT 0,  -- amount of VAT that can be deducted
    category TEXT NOT NULL,  -- restaurant, software, travel, office, professional, other
    vendor TEXT,
    description TEXT,
    source TEXT CHECK(source IN ('telegram','onedrive','email','manual')),
    onedrive_path TEXT,
    onedrive_id TEXT,
    receipt_id TEXT,  -- link to Telegram bot UUID
    matched_to_bank_id INTEGER,
    status TEXT CHECK(status IN ('pending','matched','reconciled')),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Bank transactions
CREATE TABLE bank_transactions (
    id INTEGER PRIMARY KEY,
    odoo_id INTEGER UNIQUE,
    date TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'EUR',
    description TEXT,
    vendor_inferred TEXT,
    journal_name TEXT,
    matched_to_expense_id INTEGER REFERENCES expenses(id),
    matched_to_invoice_id INTEGER REFERENCES invoices(id),
    match_confidence REAL DEFAULT 0,  -- 0.0 to 1.0
    reconciliation_status TEXT CHECK(reconciliation_status IN ('unmatched','pending','matched','reviewed')),
    notes TEXT
);

-- VAT returns
CREATE TABLE vat_returns (
    id INTEGER PRIMARY KEY,
    quarter TEXT CHECK(quarter IN ('Q1','Q2','Q3','Q4')),
    year INTEGER NOT NULL,
    total_output_vat REAL DEFAULT 0,
    total_input_vat REAL DEFAULT 0,
    net_vat_due REAL DEFAULT 0,
    status TEXT CHECK(status IN ('draft','submitted','paid')),
    due_date TEXT,
    filed_date TEXT,
    filed_by TEXT,  -- ATTC or manual
    notes TEXT
);

-- Sync log
CREATE TABLE sync_log (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,  -- odoo, onedrive, telegram, email
    action TEXT NOT NULL,  -- sync, reconcile, etc.
    records_affected INTEGER,
    status TEXT,
    error TEXT,
    synced_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

## Dashboard Views

### 1. Overview
- KPI cards: Revenue YTD, Expenses YTD, Net Profit, VAT Due
- VAT countdown: days to next filing deadline
- Reconciliation health: % matched this month
- Recent unmatched transactions (top 5)

### 2. Revenue
- Monthly revenue bar chart (vs target)
- Outstanding invoices table (aged receivables)
- Next payment expected
- Invoice detail: number, date, amount, client, status

### 3. Expenses
- Monthly expenses by category (stacked bar)
- VAT recoverable vs non-recoverable breakdown
- Recent receipts list
- Top vendors

### 4. VAT
- Quarterly summary: output VAT, input VAT, net due
- Breakdown: recoverable vs non-recoverable by category
- Filing deadline countdown
- Historical comparison

### 5. Bank & Reconciliation (MAIN FEATURE)
- Bank balance (current, from last transaction)
- Matched transactions (green, with link to source)
- Unmatched transactions (red, needs review)
- Auto-match confidence score
- Manual match UI: click transaction → select matching invoice/receipt
- Filter: by date range, matched/unmatched, amount range
- Export: CSV of reconciled transactions

### 6. Documents
- Recent receipts (from Telegram DB)
- OneDrive folder links
- ATTC correspondence
- Invoice PDFs

## Telegram Bot Guide (FOR END USER)

**Bot username:** `@agila_receipts_bot`
**Status:** Running on Raspberry Pi 5

**How to use:**
1. Open Telegram, search for `@agila_receipts_bot`
2. Send `/start` to see instructions
3. Take a photo of any receipt
4. Add a caption in this format:
   ```
   amount category DD/MM notes
   ```
   Examples:
   - `45 restaurant 12/04 client dinner`
   - `125 hotel 15/04 Berlin trip`
   - `34.50 software 01/03 Claude subscription`
   - `89 travel 20/03 flight to Barcelona`

5. Bot responds with confirmation showing:
   - Date parsed
   - Amount
   - Category
   - Filename it saved
   - OneDrive upload status

**Categories accepted:** restaurant, hotel, travel, flight, taxi, software, subscription, office, supplies, professional, other

**What happens:**
- Photo saved to `OneDrive/04_Receipts-Agila/YYYY/MM-MonthName/`
- Metadata stored in SQLite for reconciliation
- File named: `YYYY-MM-DD_category_notes_amountEUR.jpg`

**Check status:** Send `/status` to see recent receipts

**View pending uploads:** Send `/pending` to see receipts not yet uploaded to OneDrive

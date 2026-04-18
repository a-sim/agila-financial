# ITERATION-P2: Data Integrity & UI Fixes

## Overview
Four fixes for the Agila Financial Dashboard based on user review of the Expenses tab.

## Fix 1: UI — Chart Size and Mobile Responsiveness

**Problem:** The stacked bar chart on the Expenses tab is too small. Components don't adapt well to phone screens.

**Solution:** 
- Review the Expenses tab layout in `static/index.html`
- Make the stacked bar chart take more vertical space (at least 400px height, expandable)
- Ensure all components (chart, filters, table) are responsive on mobile screens (320px-428px width)
- Use CSS media queries and flexible container sizing
- Test layout at common mobile breakpoints (375px, 428px)

## Fix 2: Global Filters

**Problem:** The Expenses tab has separate filter controls for the stacked bar chart and the table below. This is redundant and confusing.

**Solution:**
- Merge the filter controls into a single filter bar at the top of the Expenses tab
- These filters should apply to BOTH the chart and the table simultaneously
- Filters needed: date range (from/to), category dropdown, search text
- When any filter changes, both the chart data and table data update together
- Remove the duplicate filter controls

## Fix 3: Data — Fix Wrong Dates

**Problem:** Many expenses in the dashboard DB have wrong dates. Specifically:
- 37 telegram-sourced expenses all have date `2026-04-13` (the upload/sync date) instead of the actual receipt date
- The receipt bot DB at `/home/asimo/.agila-telegram/receipts.db` has the CORRECT dates
- The onedrive filenames also have the correct dates (YYYYMMDD_Vendor_...)

**Root cause:** When expenses were synced from the telegram bot to the dashboard DB, some batch process used the current date instead of the receipt date.

**Solution:**
- Write a one-time script `scripts/fix_expense_dates.py` that:
  1. Reads all expenses from the dashboard DB where source='telegram'
  2. Matches them to receipts in the bot DB (by vendor + amount)
  3. Updates the dashboard expense date to match the bot receipt date_str
  4. Also checks onedrive-sourced expenses where the date doesn't match the filename date
  5. Logs all changes made
- Run the script after writing it
- After fixing, add a guard in `sync_to_dashboard()` in the receipt bot to never use current date as fallback for the date field

## Fix 4: Data — Foreign Currency Receipts

**Problem:** Some receipts in foreign currencies (DKK, NOK, etc.) are not properly converted. The Italian Corner receipt shows 804 EUR but was actually 804 DKK.

**Solution:**
- The receipt bot DB at `/home/asimo/.agila-telegram/receipts.db` has the correct original amounts and currencies:
  - Metrostation: amount_original=48.0, currency=DKK
  - Mefjord: amount_original=1447.22, currency=NOK  
  - Mors Mat AS: amount_original=1125.0, currency=NOK
  - THE ITALIAN CORNER: currently shows currency=EUR, amount_original=NULL — this is the misidentified one
- Write a one-time script `scripts/fix_currency_expenses.py` that:
  1. Finds all dashboard expenses where the vendor matches a receipt with foreign currency
  2. For THE ITALIAN CORNER specifically: the receipt was 804 DKK (not EUR). Convert to EUR using ECB rates and update
  3. For all other foreign currency receipts: verify the dashboard amount matches the EUR-converted amount from the bot
  4. Update the dashboard expenses with correct EUR amounts
  5. Update the description/notes field to show "Original: X.XX CUR → EUR amount"
- Also fix the OneDrive filename for the Italian Corner receipt (currently `20260311_THEITALIANCORNER_Restaurant_804.00EUR.jpg` should be `20260311_THEITALIANCORNER_Restaurant_804.00DKK.jpg`)

## Fix 5: Data — Import Missing 2025 Expenses

**Problem:** Q2-Q4 2025 expenses are fully missing from the dashboard. They exist on OneDrive.

**OneDrive folder IDs:**
- 2025Q2: `017IBDTVVNFP7NA5ODHVH26PQTC3B3D2XF` (4 files)
- 2025Q3: `017IBDTVULQGUFAMKND5DKILTALBHECCYX` (59 files)
- 2025Q4: `017IBDTVWBHE3LCF47ZJCZHF2YJUZ3AZCN` (98 files)
- Accounting root: `017IBDTVTRNHAVGBJHV5EKWT5VZYB5Q2KC`

**Solution:**
- Extend `scripts/sync_onedrive.py` to also scan 2025 folders, OR write a one-time import script
- The script should:
  1. Use Microsoft Graph API to list files in each 2025 quarterly folder
  2. Parse filenames using the existing `parse_filename()` function
  3. Import to the dashboard DB with source='onedrive'
  4. Skip bank statements, salary slips, VAT declarations
  5. Handle the same naming convention: YYYYMMDD_Vendor_Description_AmountCUR.ext
- Run the script after writing it

## Fix 6: Data Consistency — Cross-Application Validation

**Problem:** After fixing dates, currencies, and importing missing data, we need to ensure the dashboard is fully consistent with OneDrive and the receipt bot.

**Solution:**
- Write a validation script `scripts/validate_consistency.py` that:
  1. Lists all receipt files on OneDrive (2025 Q2-Q4, 2026 Q1-Q2)
  2. For each file, checks if a matching expense exists in the dashboard DB
  3. For telegram-sourced expenses, checks the bot DB for matching records
  4. Reports: missing expenses, duplicate expenses, amount mismatches, date mismatches
  5. Does NOT auto-fix — just reports for review

## Implementation Order

1. Fix 3 (dates) — most impactful, fixes existing data
2. Fix 4 (currencies) — fixes existing data
3. Fix 5 (import 2025) — adds missing data
4. Fix 1 (UI chart size) — frontend only
5. Fix 2 (global filters) — frontend only
6. Fix 6 (validation) — verification step

## Key Paths

- Dashboard DB: `/home/asimo/agila-financial-dashboard/agila.db`
- Dashboard static: `/home/asimo/agila-financial-dashboard/static/index.html`
- Dashboard backend: `/home/asimo/agila-financial-dashboard/backend/`
- Receipt bot DB: `/home/asimo/.agila-telegram/receipts.db`
- Receipt bot code: `/home/asimo/.agila-telegram/receipt_bot.py`
- OneDrive sync: `/home/asimo/agila-financial-dashboard/scripts/sync_onedrive.py`
- Microsoft token cache: `~/.microsoft_mcp_token_cache.json`
- Agila account ID: `87fbfc0e-dfa2-4621-aab2-319dad4e93ae.c44c0a70-24ac-4b5c-adc5-8c24d4f62e21`

## After All Fixes

- Restart the dashboard service: `sudo systemctl restart agila-financial.service`
- Verify at `http://100.124.80.84:8081/`

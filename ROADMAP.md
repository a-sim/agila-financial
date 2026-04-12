# Agila Financial Dashboard — Roadmap

## Status: v2.1 — P0 iteration completed 2026-04-12

## What Works (P0 — DONE ✅)
- ✅ DB schema migrated to v2 (vat_rate, vat_recoverable REAL, amount_residual, etc.)
- ✅ Odoo live sync: 4 invoices + 111 bank statement lines via JSON-RPC
- ✅ VAT logic: per-category rates (restaurant 0% recovery, hotel 3%, rest 17%)
- ✅ Reconciliation engine with confidence scoring (amount + date + vendor matching)
- ✅ Sync API endpoints: POST /api/sync/odoo, /api/sync/telegram, POST /api/reconcile, GET /api/sync/status
- ✅ Telegram receipts sync script (ready, 0 receipts in bot DB currently)
- ✅ Dashboard: outstanding invoice display, Sync Now button, corrected VAT breakdown
- ✅ INV/2026/00003 correctly shows as outstanding (€12,968.75)
- ✅ Deduplication-safe upsert logic for Odoo sync
- ✅ 115 bank transactions with real descriptions (Osteria, OpenAI, Tesla, Luxair, etc.)

## What's Next (Priority Order)

### P1 — Operational
1. **Telegram bot → dashboard auto-pipe** — When a receipt arrives via @agila_receipts_bot, auto-sync to expenses table
2. **OneDrive receipt scan** — Scan 01_Agila_Lux/01_Accounting/ folder for existing receipts, import into expenses
3. **VAT filing reminder** — Cron job to remind 15 days before VAT deadline
4. **Better reconciliation** — More bank txns need manual matching; add UI for manual match (click txn → select match)

### P2 — Analytics
5. **Monthly PDF report** — Generate P&L summary PDF at close of month
6. **Cash flow projection** — Model future months based on pipeline (Luxair milestone: 15 July 2026)
7. **ATTC cost tracking** — Distinguish ATTC retainer from other professional services in dashboard
8. **Expense trend charts** — Line chart for monthly spend by category

### P3 — Polish
9. **User auth** — Add basic HTTP auth (Tailscale-only for now, but belt-and-suspenders)
10. **Multi-company** — Support IVO Barcelona from same DB with separate P&L view
11. **Historical import** — Import 2024-2025 accounting data from Odoo for full-year view
12. **Revolut bank feed** — Connect Revolut Business API when API key available

## Known Issues
- Reconciliation engine only matched 0 out of 115 bank transactions (high threshold of 0.7 confidence — most txns lack vendor names that match expense entries)
- April 2026 revenue = €0 in DB (no April invoice created yet in Odoo)
- ATTC annual cost (€4,000) not broken out separately in dashboard KPIs
- Bank transactions from reconciliation_data (the xlsx-seeded Everest card data) are separate from the Odoo-synced Revolut bank data — need to merge or display both
- Bank balance (€24,053.90) sums all transactions but doesn't reflect opening balance

## Architecture
- FastAPI on port 8081 (systemd service)
- SQLite at agila.db (v2 schema)
- Odoo API via JSON-RPC (agila-consulting-sarl.odoo.com)
- Telegram bot at @agila_receipts_bot
- Dashboard: single index.html, mobile-first, Chart.js, dark theme
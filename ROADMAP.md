# Agila Financial Dashboard — Roadmap

## Status: v1.0 — Built 2026-04-12

## What Works
- FastAPI backend on port 8081 with 6 API endpoints
- SQLite database seeded with 4 Odoo invoices and 18 expense entries
- HTML dashboard (mobile-first, dark theme, Chart.js)
- systemd service configured

## What's Next (Priority Order)

### P0 — Connect Live Data
1. **Odoo live sync** — pull real invoices via Odoo XML-RPC API using stored credentials
2. **OneDrive receipts scan** — scan 01_Agila_Lux/01_Accounting/ folder for receipts, import into expenses table
3. **Revolut API** — Alejandro to provide private key; connect transactions for real bank feed

### P1 — Operational
4. **Telegram bot integration** — pipe receipts from `~/.agila-telegram/receipts.db` automatically into expenses table with VAT classification
5. **VAT filing reminder** — add email notification 10 days before VAT deadline
6. **Odoo auto-invoice creation** — trigger new invoice draft in Odoo at month-end

### P2 — Analytics
7. **Monthly PDF report** — generate P&L summary PDF at close of month
8. **Cash flow projection** — model future months based on pipeline (Luxair milestone: 15 July 2026)
9. **ATTC cost tracking** — distinguish ATTC retainer from other professional services

### P3 — Polish
10. **User auth** — add basic HTTP auth for public access
11. **Multi-company** — support IVO Barcelona from same DB with separate P&L view
12. **Historical import** — import 2024-2025 accounting data from Odoo for full-year view

## Known Gaps
- No auth on the dashboard (internal network only via Tailscale)
- VAT calculation assumes 17% on all expenses; verify per category with ATTC
- April 2026 revenue = €0 in DB (no invoice created yet)
- ATTC annual cost (€4,000) not broken out separately in expense categories

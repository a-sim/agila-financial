# Agila Financial Dashboard

Financial operations dashboard for Agila Consulting SARL. The solution combines a FastAPI backend, SQLite data model, lightweight HTML/JS frontend, and operational sync scripts for Odoo, OneDrive, Telegram, Outlook attachments, and reconciliation workflows.

## Solution Overview

- **Revenue visibility** from Odoo invoices and receivables
- **Expense capture** from OneDrive accounting folders, Telegram receipt intake, and email attachments
- **Bank reconciliation** against synced Odoo bank entries
- **VAT tracking** with Luxembourg-specific recovery logic
- **Document visibility** for receipts and accounting folders
- **Single-screen operator UI** served from the same app stack

## Architecture

```mermaid
graph TB
  %% Layer grouping
  subgraph DS[Data Sources]
    Odoo([Odoo Accounting])
    OD[(OneDrive receipt folders)]
    TG[(Telegram receipts DB)]
    Outlook([Outlook inbox with attachments])
    User([Alejandro / operator])
  end

  subgraph BE[Backend]
    API[FastAPI app\nbackend/main.py]
    Routers[API routers\nrevenue, expenses, VAT, bank, documents,\nreconciliation, emails]
    Scripts[Sync scripts\nsync_odoo, sync_onedrive, sync_telegram,\ndownload_attachments]
    Recon[Reconciliation engine\nbackend/services/reconciliation.py]
    DB[(SQLite\nagila.db)]
  end

  subgraph FE[Frontend]
    UI([Single-page dashboard\nindex.html / static UI])
    Charts[Charts and tab views\nOverview, Revenue, Expenses, VAT, Bank, Documents]
  end

  subgraph EX[External Integrations]
    MCP([mcporter + microsoft-mcp])
    Graph([Microsoft Graph API])
    PDF([PDF/text extraction\npdftotext / strings])
  end

  subgraph DEP[Deployment]
    Service[systemd service\nagila-financial.service]
    Host([Linux host / Raspberry Pi])
    Files[(Local data folder\nPDFs, attachments, statements)]
  end

  %% Flows
  User -->|views KPIs and details| UI
  UI -->|fetches JSON APIs| API
  API -->|serves dashboard + static assets| UI
  UI -->|renders tabs and charts| Charts

  API -->|routes requests| Routers
  Routers -->|read/write operational data| DB
  API -->|startup init_db| DB
  API -->|trigger sync and reconcile endpoints| Scripts
  Scripts -->|populate invoices and bank entries| DB
  Scripts -->|scan local files| Files
  Recon -->|match invoices, expenses, bank txns| DB
  Routers -->|invoke reconciliation workflow| Recon

  Odoo -->|invoice and bank data sync| Scripts
  OD -->|receipt files and metadata scan| Scripts
  TG -->|receipt imports| Scripts
  Outlook -->|attachment discovery| MCP
  Scripts -->|Outlook attachment sync| MCP
  MCP -->|mailbox and file operations| Graph
  Scripts -->|upload receipts| Graph
  Graph -->|OneDrive folder access| OD
  Scripts -->|extract amount and vendor signals| PDF

  Service -->|runs app service| API
  Host -->|hosts app and jobs| Service
  Files -->|stored statements and attachments| DB

  %% Styling
  classDef data fill:#3b82f6,stroke:#93c5fd,color:#ffffff,stroke-width:1px;
  classDef backend fill:#22c55e,stroke:#86efac,color:#062b12,stroke-width:1px;
  classDef frontend fill:#8b5cf6,stroke:#c4b5fd,color:#ffffff,stroke-width:1px;
  classDef external fill:#f59e0b,stroke:#fcd34d,color:#1f1300,stroke-width:1px;
  classDef deploy fill:#6b7280,stroke:#9ca3af,color:#ffffff,stroke-width:1px;

  class Odoo,OD,TG,Outlook,User,Files data;
  class API,Routers,Scripts,Recon,DB backend;
  class UI,Charts frontend;
  class MCP,Graph,PDF external;
  class Service,Host deploy;
```

## Component Breakdown

### Data sources
- **Odoo** provides posted outbound invoices and bank statement lines.
- **OneDrive** is the accounting source of truth for receipts, invoices, and supporting documents.
- **Telegram receipts DB** feeds receipt captures into the expenses workflow.
- **Outlook inbox attachments** extend document intake for invoices and receipts received by email.
- **Local data files** hold statements, sample PDFs, downloaded attachments, and working artifacts.

### Backend
- **`backend/main.py`** initializes FastAPI, mounts static assets, and exposes summary + health endpoints.
- **Routers** split responsibility cleanly by domain:
  - `revenue.py`
  - `expenses.py`
  - `vat.py`
  - `bank.py`
  - `documents.py`
  - `reconciliation.py`
  - `emails.py`
- **SQLite (`agila.db`)** stores invoices, expenses, bank transactions, VAT returns, sync logs, companies, and reconciliation data.
- **Reconciliation engine** matches bank movements to invoices and expenses using amount, date proximity, and fuzzy vendor matching.

### Frontend
- **`index.html` / `static/index.html`** provides a dark-themed single-page dashboard.
- Frontend tabs cover **Overview, Revenue, Expenses, VAT, Reconciliation, and Documents**.
- The UI triggers operational actions such as **Sync Now** and renders charts with Chart.js.

### Integrations and automation
- **`scripts/sync_odoo.py`** imports invoices and bank entries from Odoo.
- **`scripts/sync_onedrive.py`** scans receipt folders and infers metadata from filenames.
- **`scripts/sync_telegram.py`** imports Telegram receipts into the expense ledger.
- **`scripts/download_attachments.py` / `backend/routers/emails.py`** handle Outlook attachment download and OneDrive upload flows.
- **mcporter + microsoft-mcp + Graph API** provide Outlook and OneDrive access.
- **PDF parsing utilities** extract amounts from invoice and receipt documents.

### Deployment
- **`agila-financial.service`** runs the application as a Linux service.
- The solution is designed to run on a lightweight host with local SQLite storage and scheduled/triggered sync operations.

## Repository Structure

```text
agila-financial-dashboard/
├── backend/
│   ├── main.py
│   ├── routers/
│   └── services/
├── scripts/
├── static/
├── data/
├── database.py
├── models.py
├── index.html
└── agila-financial.service
```

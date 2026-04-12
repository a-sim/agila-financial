from pydantic import BaseModel
from typing import Optional, List


class Invoice(BaseModel):
    id: Optional[int] = None
    odoo_id: Optional[str] = None
    name: str
    type: str = "outbound"
    date: str
    due_date: Optional[str] = None
    amount_gross: float
    amount_vat: float = 0
    amount_net: float = 0
    amount_residual: float = 0
    vat_rate: float = 0
    client_supplier_name: Optional[str] = None
    status: str = "draft"
    category: str = "consulting"


class Expense(BaseModel):
    id: Optional[int] = None
    date: str
    amount: float
    amount_vat: float = 0
    category: str
    vendor: Optional[str] = None
    notes: Optional[str] = None
    description: Optional[str] = None
    vat_rate: float = 0
    vat_recoverable: float = 0
    source: str = "manual"
    receipt_id: Optional[str] = None
    status: str = "pending"


class BankTransaction(BaseModel):
    id: Optional[int] = None
    odoo_id: Optional[int] = None
    date: str
    amount: float
    currency: str = "EUR"
    partner: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    source: str = "odoo_revolut"
    reconciliation_status: str = "unmatched"
    match_confidence: float = 0


class SummaryKPIs(BaseModel):
    current_month_revenue: float
    current_month_target: float
    q1_2026_revenue: float
    q1_2026_expenses: float
    net_profit_estimate: float
    output_vat_collected: float
    input_vat_recoverable: float
    net_vat_due: float
    invoiced_not_received: float
    daily_rate: float = 625.0


class RevenueData(BaseModel):
    invoices: list
    monthly_totals: dict
    outstanding: list
    by_client: dict
    days_worked_current_month: int


class ExpenseData(BaseModel):
    expenses: list
    monthly_totals: dict
    by_category: dict
    categories: list = []
    vat_recoverable_total: float
    vat_non_recoverable_total: float


class DocumentData(BaseModel):
    receipts: list
    receipt_count_month: int
    onedrive_accounting_url: str
from pydantic import BaseModel
from typing import Optional

class Invoice(BaseModel):
    id: Optional[int] = None
    odoo_id: Optional[str] = None
    name: str
    date: str
    amount_gross: float
    amount_vat: float
    amount_net: float
    client: str
    status: str = "draft"
    category: str = "consulting"

class Expense(BaseModel):
    id: Optional[int] = None
    date: str
    amount: float
    category: str
    vendor: Optional[str] = None
    notes: Optional[str] = None
    vat_recoverable: bool = False
    onedrive_id: Optional[str] = None
    source: str = "manual"

class VATReturn(BaseModel):
    id: Optional[int] = None
    quarter: int
    year: int
    output_vat: float = 0
    input_vat: float = 0
    net_vat: float = 0
    status: str = "pending"
    due_date: Optional[str] = None
    filed_date: Optional[str] = None

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
    vat_recoverable_total: float
    vat_non_recoverable_total: float

class VATData(BaseModel):
    quarters: list
    current_quarter: dict
    days_until_deadline: int

class DocumentData(BaseModel):
    receipts: list
    receipt_count_month: int
    onedrive_accounting_url: str

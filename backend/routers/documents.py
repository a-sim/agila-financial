from fastapi import APIRouter
import sqlite3
from pathlib import Path
from models import DocumentData

router = APIRouter(prefix="/api/documents", tags=["documents"])

RECEIPTS_DB = Path.home() / ".agila-telegram" / "receipts.db"

@router.get("", response_model=DocumentData)
def get_documents():
    receipts = []
    count_month = 0

    if RECEIPTS_DB.exists():
        try:
            conn = sqlite3.connect(str(RECEIPTS_DB))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM receipts ORDER BY created_at DESC LIMIT 50")
            receipts = [dict(row) for row in cur.fetchall()]

            cur.execute("""
                SELECT COUNT(*) FROM receipts
                WHERE strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')
            """)
            count_month = cur.fetchone()[0] or 0
            conn.close()
        except Exception as e:
            receipts = []

    return DocumentData(
        receipts=receipts,
        receipt_count_month=count_month,
        onedrive_accounting_url="https://1drv.ms/f/s!AgilaAccounting"
    )

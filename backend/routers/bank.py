from fastapi import APIRouter
from backend.services.revolut_service import get_transactions

router = APIRouter(prefix="/api/bank", tags=["bank"])

@router.get("")
def get_bank():
    data = get_transactions()
    return data

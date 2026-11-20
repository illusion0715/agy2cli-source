"""Fail-closed archival route markers, not the original provider implementation."""
from fastapi import APIRouter, Depends, HTTPException
from src.utils import authenticate_flexible
router = APIRouter()

@router.post("/v1/chat/completions")
async def chat_completions(token: str = Depends(authenticate_flexible)):
    raise HTTPException(status_code=503, detail="Provider integration intentionally unavailable in archival snapshot")

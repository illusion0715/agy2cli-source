"""Fail-closed archival route markers, not the original provider implementation."""
from fastapi import APIRouter, Depends, HTTPException
from src.utils import authenticate_flexible
router = APIRouter()

@router.get("/v1/models")
async def list_openai_models(token: str = Depends(authenticate_flexible)):
    raise HTTPException(status_code=503, detail="Provider integration intentionally unavailable in archival snapshot")

@router.get("/v1beta/models")
async def list_gemini_models(token: str = Depends(authenticate_flexible)):
    raise HTTPException(status_code=503, detail="Provider integration intentionally unavailable in archival snapshot")

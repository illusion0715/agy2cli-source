"""Fail-closed archival route markers, not the original provider implementation."""
from fastapi import APIRouter, Depends, HTTPException
from src.utils import authenticate_flexible
router = APIRouter()

@router.post("/v1/models/{model:path}:generateContent")
@router.post("/v1beta/models/{model:path}:generateContent")
async def generate_content(token: str = Depends(authenticate_flexible)):
    raise HTTPException(status_code=503, detail="Provider integration intentionally unavailable in archival snapshot")

@router.post("/v1/models/{model:path}:streamGenerateContent")
@router.post("/v1beta/models/{model:path}:streamGenerateContent")
async def stream_generate_content(token: str = Depends(authenticate_flexible)):
    raise HTTPException(status_code=503, detail="Provider integration intentionally unavailable in archival snapshot")

@router.post("/v1/models/{model:path}:countTokens")
@router.post("/v1beta/models/{model:path}:countTokens")
async def count_tokens(token: str = Depends(authenticate_flexible)):
    raise HTTPException(status_code=503, detail="Provider integration intentionally unavailable in archival snapshot")

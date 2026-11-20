"""Reviewed archival subset; omitted capabilities are unavailable. See ARCHITECTURE.md."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from src.models import LoginRequest
from src.auth import verify_password
from log import log
router = APIRouter(prefix="/auth")


@router.post("/login")
async def login(request: LoginRequest):
    """用户登录（简化版：直接返回密码作为token）"""
    try:
        if await verify_password(request.password):
            # 直接使用密码作为token，简化认证流程
            return JSONResponse(content={"token": request.password, "message": "登录成功"})
        else:
            raise HTTPException(status_code=401, detail="密码错误")
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"登录失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))



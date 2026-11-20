"""Fail-closed archival route markers, not the original provider implementation."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from src.token_estimator import estimate_input_tokens
from log import log
from src.utils import authenticate_flexible
router = APIRouter()
authenticate_bearer = authenticate_flexible

@router.post("/v1/messages")
async def messages(token: str = Depends(authenticate_flexible)):
    raise HTTPException(status_code=503, detail="Provider integration intentionally unavailable in archival snapshot")

@router.post("/v1/messages/count_tokens")
async def count_tokens(
    request: Request,
    _token: str = Depends(authenticate_bearer)
):
    """
    处理Anthropic格式的token计数请求
    
    Args:
        request: FastAPI请求对象
        _token: Bearer认证令牌（由Depends验证）
    
    Returns:
        JSONResponse: 包含input_tokens的响应
    """
    try:
        payload = await request.json()
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"type": "error", "error": {"type": "invalid_request_error", "message": f"JSON 解析失败: {str(e)}"}}
        )

    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=400,
            content={"type": "error", "error": {"type": "invalid_request_error", "message": "请求体必须为 JSON object"}}
        )

    if not payload.get("model") or not isinstance(payload.get("messages"), list):
        return JSONResponse(
            status_code=400,
            content={"type": "error", "error": {"type": "invalid_request_error", "message": "缺少必填字段：model / messages"}}
        )

    try:
        client_host = request.client.host if request.client else "unknown"
        client_port = request.client.port if request.client else "unknown"
    except Exception:
        client_host = "unknown"
        client_port = "unknown"

    thinking_present = "thinking" in payload
    thinking_value = payload.get("thinking")
    thinking_summary = None
    if thinking_present:
        if isinstance(thinking_value, dict):
            thinking_summary = {
                "type": thinking_value.get("type"),
                "budget_tokens": thinking_value.get("budget_tokens"),
            }
        else:
            thinking_summary = thinking_value

    user_agent = request.headers.get("user-agent", "")
    log.info(
        f"[ANTIGRAVITY-ANTHROPIC] /messages/count_tokens 收到请求: client={client_host}:{client_port}, "
        f"model={payload.get('model')}, messages={len(payload.get('messages') or [])}, "
        f"thinking_present={thinking_present}, thinking={thinking_summary}, ua={user_agent}"
    )

    # 简单估算
    input_tokens = 0
    try:
        input_tokens = estimate_input_tokens(payload)
    except Exception as e:
        log.error(f"[ANTIGRAVITY-ANTHROPIC] token 估算失败: {e}")

    return JSONResponse(content={"input_tokens": input_tokens})

"""Reviewed archival subset; omitted capabilities are unavailable. See ARCHITECTURE.md."""
from config import get_panel_password

async def verify_password(password: str) -> bool:
    """验证密码（面板登录使用）"""
    correct_password = await get_panel_password()
    return password == correct_password



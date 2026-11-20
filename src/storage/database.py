"""Normal config/usage schemas only; no credential schema or database connection."""
import json
import time
from typing import Any, Optional
from sqlalchemy import Boolean, Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class ConfigItem(Base):
    """配置表"""
    __tablename__ = "config"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)

class UsageLog(Base):
    """请求日志表：每次 API 调用记录一条（成功与失败均记录）"""
    __tablename__ = "usage_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, index=True)

    # 请求来源
    api_format: Mapped[str] = mapped_column(String(16), default="")        # openai / gemini / anthropic
    client_ip: Mapped[str] = mapped_column(String(64), default="")
    request_id: Mapped[str] = mapped_column(String(64), default="")

    # 调用信息
    model_name: Mapped[str] = mapped_column(String(128), default="")             # 客户端请求的模型名（原始，可能带功能前缀）
    upstream_model_name: Mapped[str] = mapped_column(String(128), default="")   # 后端实际使用的真实模型名（映射后）
    credential_email: Mapped[str] = mapped_column(String(320), default="")
    is_stream: Mapped[bool] = mapped_column(Boolean, default=False)

    # 结果
    status_code: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    use_time: Mapped[float] = mapped_column(Float, default=0.0)            # 耗时（秒）

    # Token 用量
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)  # prompt 中命中缓存的 token（含在 prompt_tokens 内）

    # 失败时的错误摘要
    error_message: Mapped[str] = mapped_column(Text, default="")

    # 请求体详细日志文件索引（data/request_logs 相对路径；空=未记录）
    request_log_file: Mapped[str] = mapped_column(String(512), default="")

    __table_args__ = (
        Index("idx_ul_created_model", "created_at", "model_name"),
        Index("idx_ul_created_email", "created_at", "credential_email"),
    )

def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)

def _load_json(raw: Optional[str], default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default

async def get_database():
    raise NotImplementedError("Database connections and credential persistence are intentionally unavailable")
async def close_database():
    return None

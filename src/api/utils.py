"""Reviewed archival subset; omitted capabilities are unavailable. See ARCHITECTURE.md."""
import re
import time
from datetime import datetime, timezone
from typing import Optional

def parse_quota_reset_timestamp(error_response: dict) -> Optional[float]:
    """
    从 Antigravity API 429 错误响应中提取quota重置时间戳

    解析优先级：
    1. ErrorInfo.metadata.quotaResetTimeStamp（绝对时间戳，最精确）
    2. ErrorInfo.metadata.quotaResetDelay（相对时长，如 "154h21m14.468080073s"）
    3. RetryInfo.retryDelay（纯秒数，如 "555674.468080073s"）
    """
    try:
        error_obj = error_response.get("error", {})
        details = error_obj.get("details", [])

        error_info_metadata: Optional[dict] = None
        retry_delay_str: Optional[str] = None

        for detail in details:
            detail_type = detail.get("@type", "")
            if detail_type == "type.googleapis.com/google.rpc.ErrorInfo":
                error_info_metadata = detail.get("metadata", {})
            elif detail_type == "type.googleapis.com/google.rpc.RetryInfo":
                retry_delay_str = detail.get("retryDelay")

        unit_to_seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}

        # 优先级1: quotaResetTimeStamp（绝对时间戳）
        if error_info_metadata:
            reset_timestamp_str = error_info_metadata.get("quotaResetTimeStamp")
            if reset_timestamp_str:
                if reset_timestamp_str.endswith("Z"):
                    reset_timestamp_str = reset_timestamp_str.replace("Z", "+00:00")

                reset_dt = datetime.fromisoformat(reset_timestamp_str)
                if reset_dt.tzinfo is None:
                    reset_dt = reset_dt.replace(tzinfo=timezone.utc)

                return reset_dt.astimezone(timezone.utc).timestamp()

        # 优先级2: quotaResetDelay（相对时长，支持小数秒）
        if error_info_metadata:
            reset_delay_str = error_info_metadata.get("quotaResetDelay")
            if reset_delay_str:
                parts = re.findall(r"(\d+(?:\.\d+)?)([smhd])", reset_delay_str)
                if parts:
                    cooldown_seconds = sum(
                        float(value) * unit_to_seconds[unit] for value, unit in parts
                    )
                    if cooldown_seconds > 0:
                        return time.time() + cooldown_seconds

        # 优先级3: RetryInfo.retryDelay（纯秒数）
        if retry_delay_str:
            parts = re.findall(r"(\d+(?:\.\d+)?)([smhd])", retry_delay_str)
            if parts:
                cooldown_seconds = sum(
                    float(value) * unit_to_seconds[unit] for value, unit in parts
                )
                if cooldown_seconds > 0:
                    return time.time() + cooldown_seconds

        return None

    except Exception:
        return None



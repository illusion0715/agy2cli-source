"""Standard OAuth operations only; not connected to archived provider routes."""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode
from config import get_googleapis_proxy_url, get_oauth_proxy_url
from log import log
from src.httpx_client import get_async, post_async

class TokenError(Exception):
    """Token相关错误"""

    pass

class Credentials:
    """Caller-supplied credentials; no serialization/export helpers retained."""

    def __init__(
            self,
            access_token: str,
            refresh_token: str = None,
            client_id: str = None,
            client_secret: str = None,
            expires_at: datetime = None,
            project_id: str = None,
        ):
            self.access_token = access_token
            self.refresh_token = refresh_token
            self.client_id = client_id
            self.client_secret = client_secret
            self.expires_at = expires_at
            self.project_id = project_id

            # 反代配置将在使用时异步获取
            self.oauth_base_url = None
            self.token_endpoint = None

    def is_expired(self) -> bool:
            """检查token是否过期"""
            if not self.expires_at:
                return True

            # 提前3分钟认为过期
            buffer = timedelta(minutes=3)
            return (self.expires_at - buffer) <= datetime.now(timezone.utc)

    async def refresh_if_needed(self) -> bool:
            """如果需要则刷新token"""
            if not self.is_expired():
                return False

            if not self.refresh_token:
                raise TokenError("需要刷新令牌但未提供")

            await self.refresh()
            return True

    async def refresh(self):
            """刷新访问令牌"""
            if not self.refresh_token:
                raise TokenError("无刷新令牌")

            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            }

            try:
                oauth_base_url = await get_oauth_proxy_url()
                token_url = f"{oauth_base_url.rstrip('/')}/token"
                response = await post_async(
                    token_url,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response.raise_for_status()

                token_data = response.json()
                self.access_token = token_data["access_token"]

                if "expires_in" in token_data:
                    expires_in = int(token_data["expires_in"])
                    current_utc = datetime.now(timezone.utc)
                    self.expires_at = current_utc + timedelta(seconds=expires_in)
                    log.debug(
                        f"Token刷新: 当前UTC时间={current_utc.isoformat()}, "
                        f"有效期={expires_in}秒, "
                        f"过期时间={self.expires_at.isoformat()}"
                    )

                if "refresh_token" in token_data:
                    self.refresh_token = token_data["refresh_token"]

                log.debug(f"Token刷新成功，过期时间: {self.expires_at}")

            except Exception as e:
                error_msg = str(e)
                status_code = None
                if hasattr(e, 'response') and hasattr(e.response, 'status_code'):
                    status_code = e.response.status_code
                    error_msg = f"Token刷新失败 (HTTP {status_code}): {error_msg}"
                else:
                    error_msg = f"Token刷新失败: {error_msg}"

                log.error(error_msg)
                token_error = TokenError(error_msg)
                token_error.status_code = status_code
                raise token_error

class Flow:
    """OAuth流程类"""

    def __init__(
        self, client_id: str, client_secret: str, scopes: List[str], redirect_uri: str = None
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.scopes = scopes
        self.redirect_uri = redirect_uri

        # 反代配置将在使用时异步获取
        self.oauth_base_url = None
        self.token_endpoint = None
        self.auth_endpoint = "https://accounts.google.com/o/oauth2/auth"

        self.credentials: Optional[Credentials] = None

    def get_auth_url(self, state: str = None, **kwargs) -> str:
        """生成授权URL"""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(self.scopes),
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        }

        if state:
            params["state"] = state

        params.update(kwargs)
        return f"{self.auth_endpoint}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> Credentials:
        """用授权码换取token"""
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "code": code,
            "grant_type": "authorization_code",
        }

        try:
            oauth_base_url = await get_oauth_proxy_url()
            token_url = f"{oauth_base_url.rstrip('/')}/token"
            response = await post_async(
                token_url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            response.raise_for_status()

            token_data = response.json()

            # 计算过期时间
            expires_at = None
            if "expires_in" in token_data:
                expires_in = int(token_data["expires_in"])
                expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

            # 创建凭证对象
            self.credentials = Credentials(
                access_token=token_data["access_token"],
                refresh_token=token_data.get("refresh_token"),
                client_id=self.client_id,
                client_secret=self.client_secret,
                expires_at=expires_at,
            )

            return self.credentials

        except Exception as e:
            error_msg = f"获取token失败: {str(e)}"
            log.error(error_msg)
            raise TokenError(error_msg)

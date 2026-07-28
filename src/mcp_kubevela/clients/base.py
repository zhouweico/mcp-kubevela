"""VelaUX 客户端基础实现。

VelaUX 使用 JWT 认证，令牌全生命周期由客户端自动管理：
- 首次请求前用 VELA_USERNAME / VELA_PASSWORD 调 POST /api/v1/auth/login 换取
  accessToken / refreshToken 并缓存在内存；
- 收到 401 时先尝试 GET /api/v1/auth/refresh_token（RefreshToken 头）续期，
  失败则重新登录，然后重放原请求（最多重试一次）；
- 用 asyncio.Lock 防止并发场景下重复登录。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"


class VelaAuthError(Exception):
    """登录 / 续期失败"""


class VelaApiError(Exception):
    """VelaUX 业务错误（携带 BusinessCode）"""

    def __init__(self, status_code: int, business_code: int, message: str) -> None:
        self.status_code = status_code
        self.business_code = business_code
        self.message = message
        super().__init__(f"[{status_code}/{business_code}] {message}")


class VelaClientBase:
    """VelaUX API 客户端基类：连接信息、JWT 生命周期、请求重放。"""

    def __init__(self) -> None:
        self.base_url = os.getenv("VELA_URL", "http://localhost:8000").rstrip("/")
        self.username = os.getenv("VELA_USERNAME", "")
        self.password = os.getenv("VELA_PASSWORD", "")
        self.timeout = float(os.getenv("VELA_TIMEOUT", "30"))

        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._auth_lock = asyncio.Lock()
        self._http = httpx.AsyncClient(timeout=self.timeout)

    # ---- JWT 生命周期 ----
    async def _login(self) -> None:
        if not self.username or not self.password:
            raise VelaAuthError(
                "缺少登录凭证，请设置环境变量 VELA_USERNAME 和 VELA_PASSWORD"
            )
        resp = await self._http.post(
            f"{self.base_url}{API_PREFIX}/auth/login",
            json={"username": self.username, "password": self.password},
        )
        if resp.status_code != 200:
            raise VelaAuthError(
                f"登录 VelaUX 失败（HTTP {resp.status_code}）：请检查 VELA_URL / 用户名密码"
            )
        data = resp.json()
        self._access_token = data.get("accessToken")
        self._refresh_token = data.get("refreshToken")
        if not self._access_token:
            raise VelaAuthError("登录响应缺少 accessToken")
        logger.info("VelaUX 登录成功：user=%s", self.username)

    async def _refresh(self) -> bool:
        """尝试用 refreshToken 续期，成功返回 True。"""
        if not self._refresh_token:
            return False
        resp = await self._http.get(
            f"{self.base_url}{API_PREFIX}/auth/refresh_token",
            headers={"RefreshToken": self._refresh_token},
        )
        if resp.status_code != 200:
            return False
        data = resp.json()
        token = data.get("accessToken")
        if not token:
            return False
        self._access_token = token
        self._refresh_token = data.get("refreshToken") or self._refresh_token
        logger.info("VelaUX accessToken 已续期")
        return True

    async def _ensure_token(self) -> None:
        if self._access_token:
            return
        async with self._auth_lock:
            if not self._access_token:
                await self._login()

    async def _reauth(self) -> None:
        """401 后的重新认证：先 refresh，失败再 login。"""
        async with self._auth_lock:
            if await self._refresh():
                return
            self._access_token = None
            await self._login()

    # ---- 统一请求入口 ----
    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
        _retried: bool = False,
    ) -> Any:
        """发起认证请求。path 形如 '/applications'（不含 /api/v1 前缀）。

        - 自动携带 Bearer accessToken；
        - 401 时自动续期/重登录并重放一次；
        - 非 2xx 时解析 bcode 错误结构抛出 VelaApiError。
        """
        await self._ensure_token()
        # 过滤 None 值查询参数
        query = {k: v for k, v in (params or {}).items() if v is not None} or None
        resp = await self._http.request(
            method,
            f"{self.base_url}{API_PREFIX}{path}",
            params=query,
            json=json_body,
            headers={"Authorization": f"Bearer {self._access_token}"},
        )

        if resp.status_code == 401 and not _retried:
            await self._reauth()
            return await self.request(
                method, path, params=params, json_body=json_body, _retried=True
            )

        if resp.status_code >= 400:
            business_code, message = self._parse_bcode(resp)
            raise VelaApiError(resp.status_code, business_code, message)

        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    @staticmethod
    def _parse_bcode(resp: httpx.Response) -> tuple[int, str]:
        """解析 VelaUX 错误结构 {"BusinessCode": int, "Message": str}。"""
        try:
            data = resp.json()
            return int(data.get("BusinessCode", 0)), str(data.get("Message", resp.text))
        except Exception:
            return 0, resp.text or f"HTTP {resp.status_code}"

    async def aclose(self) -> None:
        await self._http.aclose()

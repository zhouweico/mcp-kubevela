"""HTTP 传输的简单 Bearer Token 认证中间件（纯 ASGI 实现）。

仅用于保护 sse / streamable-http 两种 HTTP 传输的接口，stdio 传输不受影响。
认证方式：请求头携带 `Authorization: Bearer <token>`，
兼容 `X-Auth-Token: <token>` 与 `X-MCP-Token: <token>`。

设计为纯 ASGI 中间件，避免依赖具体版本的 Starlette 中间件 API，
可直接包裹 FastMCP 返回的 sse_app() / streamable_http_app()。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class TokenAuthMiddleware:
    """基于固定 Token 的 ASGI 认证中间件。

    - 非 HTTP 请求（如 lifespan、websocket）直接放行，保证应用生命周期正常。
    - 健康检查路径（默认 /health）免鉴权，方便容器探活。
    - 其余 HTTP 请求必须携带正确 Token，否则返回 401。
    """

    def __init__(
        self,
        app: ASGIApp,
        token: str,
        health_path: str = "/health",
    ) -> None:
        self.app = app
        self._token = token
        self._health_path = health_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            # lifespan / websocket 等直接透传，确保 session manager 生命周期正常
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path == self._health_path:
            await self._respond_ok(send)
            return

        if not self._is_authorized(scope):
            await self._respond_unauthorized(send)
            return

        await self.app(scope, receive, send)

    def _is_authorized(self, scope: Scope) -> bool:
        headers = {k.lower(): v for k, v in scope.get("headers", [])}

        auth = headers.get(b"authorization", b"").decode("latin-1").strip()
        if auth.lower().startswith("bearer "):
            if _constant_equals(auth[7:].strip(), self._token):
                return True

        for name in (b"x-auth-token", b"x-mcp-token"):
            value = headers.get(name, b"").decode("latin-1").strip()
            if value and _constant_equals(value, self._token):
                return True

        return False

    async def _respond_unauthorized(self, send: Send) -> None:
        body = b'{"error":"unauthorized","message":"missing or invalid MCP auth token"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b'Bearer realm="mcp"'),
                    (b"content-length", str(len(body)).encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def _respond_ok(self, send: Send) -> None:
        body = b'{"status":"ok"}'
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _constant_equals(a: str, b: str) -> bool:
    """常量时间比较，降低时序攻击风险。"""
    import hmac

    return hmac.compare_digest(a, b)

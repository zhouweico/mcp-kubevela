"""VelaUX 客户端工厂（单例缓存）"""

from typing import Optional

from .velaux import VelaUXClient

_cached_client: Optional[VelaUXClient] = None


async def get_vela_client() -> VelaUXClient:
    """获取 VelaUX 客户端单例。

    JWT 登录是惰性的：首次实际请求时才会调 /auth/login，
    因此这里直接构造即可，构造本身不产生网络 IO。
    """
    global _cached_client
    if _cached_client:
        return _cached_client

    _cached_client = VelaUXClient()
    return _cached_client

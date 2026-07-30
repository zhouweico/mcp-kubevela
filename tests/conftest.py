"""测试公共配置：确保 httpx2 兼容 respx 等 httpx 生态库。

respx 作为 pytest 插件在 conftest.py 之前加载，导致 httpx / httpcore
已被导入。此处先清除这些模块缓存，再调用 alias_httpx() 使 `import httpx`
全局解析为 httpx2，最后重新导入 respx 使其 patch httpx2 的 transport。
"""

import sys

# 清除 respx 插件预加载的 httpx / httpcore / respx 模块
for _key in list(sys.modules.keys()):
    if (
        _key == "httpx"
        or _key.startswith("httpx.")
        or _key == "httpcore"
        or _key.startswith("httpcore.")
        or _key == "respx"
        or _key.startswith("respx.")
    ):
        del sys.modules[_key]

import httpx2  # noqa: E402

httpx2.alias_httpx()  # noqa: E402

# 重新导入 respx，使其 patch 到 httpx2（而非原始 httpx）
import respx  # noqa: E402,F401

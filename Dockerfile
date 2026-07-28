# syntax=docker/dockerfile:1

##########  构建阶段  ##########
FROM python:3.12-slim AS builder

WORKDIR /app

# 仅复制构建所需文件，最大化利用缓存
COPY pyproject.toml README.md ./
COPY src ./src

# 构建 wheel 并安装到独立前缀，便于拷贝到运行阶段
RUN pip install --no-cache-dir --upgrade pip build \
    && pip wheel --no-cache-dir --no-deps --wheel-dir /wheels . \
    && pip install --no-cache-dir --prefix=/install .


##########  运行阶段  ##########
FROM python:3.12-slim AS runtime

# OCI 元数据（构建时可传入 --build-arg VERSION / VCS_REF / BUILD_DATE 覆盖默认值）
LABEL org.opencontainers.image.source="https://github.com/zhouweico/mcp-kubevela" \
      org.opencontainers.image.title="mcp-kubevela" \
      org.opencontainers.image.description="MCP Server for KubeVela (VelaUX) application delivery (stdio/sse/streamable-http, token auth)" \
      org.opencontainers.image.url="https://github.com/zhouweico/mcp-kubevela" \
      org.opencontainers.image.licenses="MIT"

# 运行时环境变量默认值（可在 docker run / compose 中覆盖）
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8080

WORKDIR /app

# 拷贝已安装的依赖与包
COPY --from=builder /install /usr/local

# 使用非 root 用户运行
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8080

# 健康检查：HTTP 传输下 /health 免鉴权返回 200
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; \
port=os.getenv('MCP_PORT','8080'); \
sys.exit(0) if os.getenv('MCP_TRANSPORT','stdio')=='stdio' else \
sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=3).status==200 else 1)"

# 入口：通过控制台脚本启动，具体协议由 MCP_TRANSPORT 决定
ENTRYPOINT ["mcp-kubevela"]

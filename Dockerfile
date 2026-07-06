# ---- Build stage ----
FROM python:3.11 AS builder

# Use Aliyun mirror for faster downloads in China
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/

WORKDIR /build
# 先只 COPY pyproject.toml — 依赖只跟它有关
COPY pyproject.toml .
# pip install 需要 README.md（pyproject.toml 声明了 readme = "README.md"），
# 放一个临时的占位，避免 README.md 改动触发重新安装全部依赖
RUN echo "# placeholder" > README.md && \
    mkdir -p src && pip install "." && \
    pip show financial-qa-mvp | grep Location | cut -d' ' -f2 > /site-packages-path.txt
# 再 COPY 真正的 README.md（此后的变动不影响上层的 pip 缓存）
COPY README.md .

# ---- Runtime stage ----
FROM python:3.11-slim

# Use Aliyun mirror for faster downloads in China
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/

WORKDIR /app

# Copy installed packages from builder (preserves compiled native extensions)
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy source code
COPY src/ src/
COPY deploy/ deploy/

# Volume mount points
VOLUME ["/data/chroma", "/data/logs"]

EXPOSE 8000
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

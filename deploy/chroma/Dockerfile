# ChromaDB 独立服务器镜像
# 使用与 app 容器相同的 chromadb 版本，确保 HTTP API 兼容
FROM python:3.11-slim

RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/
RUN pip install --no-cache-dir chromadb==1.5.9

EXPOSE 8000

VOLUME ["/chroma/data"]

CMD ["chroma", "run", "--path", "/chroma/data", "--host", "0.0.0.0", "--port", "8000"]

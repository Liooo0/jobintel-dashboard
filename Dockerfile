# 合成 demo 数据的求职数据仪表盘（无真实数据）。
# 构建时生成 demo/jobintel_demo.db，容器内开箱可用。
# 说明：本镜像仅做过静态检查，未在装有 Docker 的机器上执行 build（开发机无 docker）。
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./
COPY static ./static
COPY scripts ./scripts

# 构建期生成合成 demo 数据库（不依赖仓库里是否带 .db 文件）
RUN python3 scripts/make_demo_db.py \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/api/healthz', timeout=2)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]

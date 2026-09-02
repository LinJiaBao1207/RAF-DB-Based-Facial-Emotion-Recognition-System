# 推理后端 — Python 3.8 + TensorFlow 2.10.1（与本地验证环境一致）
# 构建上下文为仓库根目录: docker compose build backend

FROM python:3.8-slim-bullseye

WORKDIR /app

# OpenCV / TensorFlow 运行时依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt \
    && pip install --no-cache-dir "tensorflow==2.10.1" gunicorn==21.2.0

COPY backend /app/backend

WORKDIR /app/backend
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN chmod +x /app/backend/docker-entrypoint.sh

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/api/health')" || exit 1

ENTRYPOINT ["/app/backend/docker-entrypoint.sh"]

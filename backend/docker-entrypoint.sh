#!/bin/sh
set -e

cd /app/backend

if [ ! -d /app/models ] || [ -z "$(ls -A /app/models 2>/dev/null)" ]; then
  echo "⚠️  警告: /app/models 为空或未挂载，推理接口可能无法加载模型。"
  echo "    请将权重放入仓库 models/ 目录，或在 docker compose 中正确挂载卷。"
fi

echo "⏳ 预热模型（首次启动可能较慢）..."
python -c "
from src.api.app import warmup_models
warmup_models()
" || echo "⚠️  模型预热失败，服务仍将启动（可在模型就绪后重试）。"

echo "🚀 启动 Gunicorn..."
exec gunicorn \
  --bind 0.0.0.0:5000 \
  --workers 1 \
  --threads 4 \
  --timeout 300 \
  --keep-alive 5 \
  "src.api.app:app"

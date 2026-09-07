#!/usr/bin/env bash
# 一键安装：docker compose 起全套（Web + 自带 SearXNG）
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || { cp .env.example .env; echo "已生成 .env —— 请填入模型网关后重跑本脚本"; exit 1; }
grep -q "^NOVEL_GW1_URL=http" .env || { echo "✗ .env 里 NOVEL_GW1_URL 还没填"; exit 1; }

if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
  echo "▸ docker compose 起全套（含自带 SearXNG）"
  docker compose up -d --build
  echo "▸ 等待就绪…"; sleep 8
  curl -fsS "http://127.0.0.1:${NOVEL_PORT:-60001}/api/health" && echo
  echo "✓ http://127.0.0.1:${NOVEL_PORT:-60001}/"
else
  echo "▸ 未检测到 docker，走本机模式（检索将自动降级为纯内部记忆）"
  pip install -r requirements.txt
  bash scripts/serve.sh start
fi

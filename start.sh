#!/usr/bin/env bash
# 实验画布 · macOS/Linux 启动
set -e
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "需要 Python 3.9+。macOS/Linux 一般自带 python3；没有的话先装 Python。"
  exit 1
fi

PORT="${LABFLOW_PORT:-43300}"
export LABFLOW_OPEN=1
echo "实验画布共享服务启动中……"
echo "本机：http://127.0.0.1:${PORT}"
echo "局域网：http://<本机IP>:${PORT}（启动日志里会打印）"
echo "浏览器会自动打开；按 Ctrl+C 停止。"
exec "$PY" server.py

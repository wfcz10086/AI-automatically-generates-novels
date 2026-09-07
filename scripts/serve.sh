#!/usr/bin/env bash
# 启停服务。用 pidfile，避免 pkill -f 误杀自身命令行。
set -euo pipefail
cd "$(dirname "$0")/.."
PID=.server.pid
PORT="${NOVEL_PORT:-60001}"
case "${1:-start}" in
  start)
    if [ -f $PID ] && kill -0 "$(cat $PID)" 2>/dev/null; then echo "已在运行 $(cat $PID)"; exit 0; fi
    mkdir -p reports
    NOVEL_PORT="$PORT" setsid nohup python3 server/app.py > reports/server.log 2>&1 < /dev/null &
    echo $! > $PID; sleep 3
    echo "started $(cat $PID) → http://127.0.0.1:$PORT/" ;;
  stop)
    if [ -f $PID ]; then kill "$(cat $PID)" 2>/dev/null || true; rm -f $PID; fi
    echo stopped ;;
  restart) "$0" stop; sleep 1; "$0" start ;;
  status)
    if [ -f $PID ] && kill -0 "$(cat $PID)" 2>/dev/null; then echo "running $(cat $PID)"; else echo stopped; fi ;;
esac
